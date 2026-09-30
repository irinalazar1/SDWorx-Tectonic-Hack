"""Service layer: the application's use cases.

Services depend on abstractions handed to them by the container (policy, detection
engine, clock) and on repositories from the unit of work. They know nothing about
HTTP, FastAPI or SQL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .detection import DetectionEngine
from .domain import Conflict, InvalidAction, NotFound, ResolveCommand, User
from .ports import Clock
from .repositories import UnitOfWork
from .security.policy import AccessPolicy

MIN_ACCEPT_NOTE = 10

ACTIONS = {
    "conflict": {"keep", "accept"},
    "duplicate": {"keep", "accept"},
    "outdated": {"retire", "reconfirm"},
    "missing": {"assign", "accept"},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(clock: Clock) -> str:
    return clock().replace(microsecond=0).isoformat()


class DetectionService:
    def __init__(self, engine: DetectionEngine, clock: Clock = utc_now):
        self.engine = engine
        self.clock = clock

    def run(self, uow: UnitOfWork, actor_id: str | None = None) -> dict:
        snapshot = uow.catalog.snapshot()
        candidates = self.engine.detect(snapshot)
        uow.issues.sync(candidates, actor_id, _stamp(self.clock))
        return {"engine": self.engine.name, "as_of": snapshot.as_of.isoformat(),
                **self.engine.summarize(candidates)}


class DirectoryService:
    def demo_users(self, uow: UnitOfWork) -> list[dict]:
        return [u.public() for u in uow.users.list_active()]


class ItemService:
    def __init__(self, policy: AccessPolicy):
        self.policy = policy

    def detail(self, uow: UnitOfWork, user: User, item_id: str) -> dict:
        item = uow.catalog.item_with_owner(item_id)
        if item is None:
            raise NotFound("Item not found")
        self.policy.ensure_can_view(user, item["country"], "Item")
        item["claims"] = uow.catalog.claims(item_id)
        item["open_issues"] = [
            {"id": i["id"], "type": i["type"], "title": i["title"], "pressure": i["pressure"]}
            for i in uow.issues.open_touching(item["country"], item_id)]
        item["replaced_by"] = uow.catalog.item_brief(item["replaced_by_id"])
        return item


class IssueService:
    def __init__(self, policy: AccessPolicy, detection: DetectionService, clock: Clock = utc_now):
        self.policy = policy
        self.detection = detection
        self.clock = clock

    # ------------------------------------------------------------------ reads
    def _scrub(self, uow: UnitOfWork, user: User, issue: dict) -> dict:
        """Remove references to knowledge outside the user's scope (e.g. a gap that points
        at another country's policy), so ids from other scopes never leave the server."""
        ref = issue["details"].get("reference_item")
        if ref:
            brief = uow.catalog.item_brief(ref)
            if brief is None or not self.policy.can_view(user, brief["country"]):
                issue["details"]["reference_item"] = None
        return issue

    def map_view(self, uow: UnitOfWork, user: User) -> dict:
        items = uow.catalog.items_for_map(user.scopes)
        return {"as_of": uow.catalog.as_of().isoformat(),
                "start": min((i["created_at"] for i in items), default=None),
                "topics": uow.catalog.topics(), "items": items,
                "issues": [self._scrub(uow, user, i) for i in uow.issues.in_scopes(user.scopes)]}

    def _load(self, uow: UnitOfWork, user: User, issue_id: str) -> dict:
        issue = uow.issues.get(issue_id)
        if issue is None:
            raise NotFound("Issue not found")
        self.policy.ensure_can_view(user, issue["country"], "Issue")
        return issue

    def detail(self, uow: UnitOfWork, user: User, issue_id: str) -> dict:
        issue = self._scrub(uow, user, self._load(uow, user, issue_id))
        names = uow.users.names()
        active = uow.users.active_ids()
        clients = uow.catalog.client_names(user.scopes)
        ids = issue["item_ids"]

        items = []
        for item in uow.catalog.items_by_ids(ids):
            item["owner_name"] = names.get(item["owner_id"])
            item["owner_active"] = item["owner_id"] in active
            item["claims"] = uow.catalog.claims(item["id"])
            item["usage_by_month"] = uow.catalog.usage_by_month(item["id"])
            item["recent_clients"] = sorted({clients.get(c) for c in uow.catalog.recent_client_ids(item["id"])} - {None})
            items.append(item)
        items.sort(key=lambda i: ids.index(i["id"]))

        for q in issue["details"].get("questions", []):
            q["author_name"] = names.get(q["author_id"])
            q["client_name"] = clients.get(q["client_id"])
        ref = issue["details"].get("reference_item")
        issue["details"]["reference"] = uow.catalog.item_brief(ref) if ref else None
        issue["assigned_to_name"] = names.get(issue["assigned_to"])
        issue["resolved_by_name"] = names.get(issue["resolved_by"])
        issue["items"] = items
        issue["history"] = [{**h, "actor_name": names.get(h["actor_id"], "Fault Lines")}
                            for h in uow.audit.history([issue_id, *ids])]
        issue["allowed_actions"] = (sorted(ACTIONS[issue["type"]])
                                    if self.policy.can_resolve(user, issue["country"])
                                    and issue["state"] in ("detected", "assigned") else [])
        return issue

    # ------------------------------------------------------------------ resolve
    def resolve(self, uow: UnitOfWork, user: User, issue_id: str, cmd: ResolveCommand) -> dict:
        uow.begin_write()  # serialise concurrent resolutions of the same data
        issue = self._load(uow, user, issue_id)
        item_ids = issue["item_ids"]
        self.policy.ensure_can_resolve(user, issue["country"], uow.catalog.item_countries(item_ids))
        if cmd.action not in ACTIONS[issue["type"]]:
            raise InvalidAction(f"Action '{cmd.action}' is not valid for a {issue['type']} issue.")
        if issue["state"] in ("resolved", "accepted"):
            raise Conflict("This issue is already closed.")

        stamp, today = _stamp(self.clock), self.clock().date()
        note = (cmd.note or "").strip()[:500]
        log = uow.audit.log

        if cmd.action == "assign":
            if not uow.issues.assign_if_available(issue_id, user.id):
                raise Conflict("This issue is already assigned to someone else.")
            log(stamp, user.id, "issue.assigned", issue_id, note)
            self.detection.run(uow, actor_id=user.id)
            return {"state": "assigned"}

        # 1. Validate the whole change before touching anything.
        items = {i["id"]: i for i in uow.catalog.items_by_ids(item_ids)}
        if cmd.action == "keep":
            kept = items.get(cmd.keep_item_id or "")
            if kept is None:
                raise InvalidAction("keep_item_id must be one of the items in this issue.")
            if kept["state"] != "active":
                raise InvalidAction("Only an active item can be kept as the source of truth.")
            resolution, state = f"Kept “{kept['title']}” v{kept['version']} as the single source of truth.", "resolved"
        elif cmd.action == "retire":
            if items[item_ids[0]]["state"] == "retired":
                raise InvalidAction("This item is already retired.")
            resolution, state = "Retired: removed from circulation.", "resolved"
        elif cmd.action == "reconfirm":
            if items[item_ids[0]]["state"] != "active":
                raise InvalidAction("Only an active item can be reconfirmed. Retire it instead.")
            resolution, state = "Reconfirmed as correct", "resolved"
        else:  # accept
            if len(note) < MIN_ACCEPT_NOTE:
                raise InvalidAction(f"Explain in at least {MIN_ACCEPT_NOTE} characters why this is acceptable.")
            resolution, state = f"Accepted: {note}", "accepted"

        # 2. Claim the issue atomically; a concurrent resolver loses here, before any item changes.
        if not uow.issues.close_if_open(issue_id, state, stamp, user.id, resolution):
            raise Conflict("Someone else resolved this issue first.")

        # 3. Apply the lifecycle change to the knowledge items.
        if cmd.action == "keep":
            new_state = "deprecated" if issue["type"] == "conflict" else "retired"
            for other in item_ids:
                if other != cmd.keep_item_id and items[other]["state"] == "active":
                    uow.catalog.set_state(other, new_state, today.isoformat(), replaced_by=cmd.keep_item_id)
                    log(stamp, user.id, f"item.{new_state}", other, f"replaced by {cmd.keep_item_id}")
        elif cmd.action == "retire":
            uow.catalog.set_state(item_ids[0], "retired", today.isoformat())
            log(stamp, user.id, "item.retired", item_ids[0])
        elif cmd.action == "reconfirm":
            item = uow.catalog.item_with_owner(item_ids[0])
            new_review = (today + timedelta(days=365)).isoformat()
            uow.catalog.set_review(item["id"], new_review)
            detail = f"reviewed, next review {new_review}"
            if not item["owner_active"]:
                uow.catalog.set_owner(item["id"], user.id)
                detail += f", ownership taken over by {user.name}"
            log(stamp, user.id, "item.reconfirmed", item["id"], detail)
            resolution = f"Reconfirmed as correct; {detail}."

        if note and cmd.action != "accept":
            resolution += f" Note: {note}"
        uow.issues.set_resolution(issue_id, resolution)
        log(stamp, user.id, f"issue.{state}", issue_id, resolution)
        summary = self.detection.run(uow, actor_id=user.id)
        return {"state": state, "resolution": resolution, "detection": summary}
