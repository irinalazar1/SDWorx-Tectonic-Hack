"""Fault Lines detection engine.

Scans the knowledge catalog and makes four kinds of weak spots visible:

* conflict  - two active items give different answers on the same subject and scope
* duplicate - the same content is maintained twice (future conflicts)
* outdated  - superseded knowledge still in use, overdue reviews, owners who left
* missing   - questions people keep asking that no active item answers
                (including "covered in one country, not in the other")

Each finding becomes an issue with a stable id, the date it arose (for the
time-lapse), and a pressure score based on how much it is actually used.

The engine is pure: it receives a Snapshot and returns candidate issues. It has no
database access and no global configuration; the conflict judge is injected.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import timedelta
from itertools import combinations

from .domain import Snapshot, split
from .ports import ConflictJudge
from .textsim import TfIdf, cosine, tokens

DUPLICATE_THRESHOLD = 0.85
ANSWERED_THRESHOLD = 0.2
ELSEWHERE_THRESHOLD = 0.45  # clearly answered by another country's policy
QUESTION_CLUSTER_THRESHOLD = 0.2
MIN_QUESTIONS_FOR_GAP = 2

TYPE_WEIGHT = {"conflict": 1.3, "outdated": 1.1, "missing": 1.0, "duplicate": 0.8}


def fingerprint(kind: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join((kind, *sorted(parts))).encode()).hexdigest()[:10]
    return f"{kind[:3]}-{digest}"


def humanize(subject: str) -> str:
    parts = [p for p in subject.split(".") if p not in {"be", "nl"}]
    return " ".join(p.replace("_", " ") for p in parts)


def _active(items: dict) -> list[dict]:
    return [i for i in items.values() if i["state"] == "active"]


def _lineage(items: dict, a: str, b: str) -> bool:
    """True when one item is a version of the other."""
    def chain(x: str) -> set[str]:
        seen = set()
        while x and x not in seen:
            seen.add(x)
            x = items.get(x, {}).get("supersedes_id") or ""
        return seen
    return b in chain(a) or a in chain(b)


# --------------------------------------------------------------------------- detectors
def detect_conflicts(ctx: Snapshot, judge: ConflictJudge) -> list[dict]:
    items, claims = ctx.items, ctx.claims
    by_subject = defaultdict(list)
    for item in _active(items):
        for claim in claims[item["id"]]:
            by_subject[(item["country"], claim["subject"])].append((item, claim))

    found = []
    for (country, subject), entries in by_subject.items():
        for (ia, ca), (ib, cb) in combinations(entries, 2):
            if ia["id"] == ib["id"] or _lineage(items, ia["id"], ib["id"]):
                continue
            if ca["value"].strip().lower() == cb["value"].strip().lower():
                continue
            verdict = judge.judge(ia, ca, ib, cb)
            if not verdict.contradicts:
                continue
            explanation = verdict.explanation
            first, second = sorted([(ia, ca), (ib, cb)], key=lambda p: p[0]["created_at"])
            found.append({
                "id": fingerprint("conflict", ia["id"], ib["id"], subject),
                "type": "conflict", "country": country, "topic": ia["topic"],
                "title": f"Conflicting answers: {humanize(subject)}",
                "explanation": explanation or (
                    f"“{first[0]['title']}” and “{second[0]['title']}” give different answers. "
                    f"Anyone relying on the older item gives the wrong answer."),
                "details": {"subject": subject, "claims": [
                    {"item_id": it["id"], "value": cl["value"], "text": cl["text"]}
                    for it, cl in (first, second)]},
                "item_ids": [first[0]["id"], second[0]["id"]],
                "arose_at": max(ia["created_at"], ib["created_at"]),
            })
    return found


def detect_duplicates(ctx: Snapshot) -> list[dict]:
    items, users = ctx.items, ctx.users
    active = _active(items)
    tfidf = TfIdf([i["body"] for i in active])
    found = []
    for (x, vx), (y, vy) in combinations(list(zip(active, tfidf.vectors)), 2):
        if x["country"] != y["country"] or _lineage(items, x["id"], y["id"]):
            continue
        sim = cosine(vx, vy)
        if sim < DUPLICATE_THRESHOLD:
            continue
        first, second = sorted([x, y], key=lambda i: i["created_at"])
        owners = {users[i["owner_id"]]["name"] for i in (first, second)}
        found.append({
            "id": fingerprint("duplicate", x["id"], y["id"]),
            "type": "duplicate", "country": x["country"], "topic": first["topic"],
            "title": f"Maintained twice: {first['title']}",
            "explanation": (
                f"“{second['title']}” is a {round(sim * 100)}% copy of “{first['title']}”"
                + (f", maintained by different owners ({' and '.join(sorted(owners))})" if len(owners) > 1 else "")
                + ". When one gets updated and the other does not, this becomes a conflict."),
            "details": {"similarity": round(sim, 3)},
            "item_ids": [first["id"], second["id"]],
            "arose_at": second["created_at"],
        })
    return found


def detect_outdated(ctx: Snapshot) -> list[dict]:
    items, users, usage, chats = ctx.items, ctx.users, ctx.usage, ctx.chats
    today = ctx.as_of.isoformat()
    found = []
    for item in items.values():
        reasons = []
        if item["state"] == "active":
            if item["review_by"] and item["review_by"] < today:
                reasons.append({"kind": "review_overdue", "since": item["review_by"],
                                "text": f"Review was due on {item['review_by']} and never happened."})
            owner = users.get(item["owner_id"])
            if owner and not owner["active"]:
                left = owner["left_at"] or item["created_at"]
                reasons.append({"kind": "owner_left", "since": left,
                                "text": f"Owner {owner['name']} left the company on {left}; nobody maintains it."})
        elif item["state"] in ("deprecated", "retired") and item["state_changed_at"]:
            since = item["state_changed_at"]
            later_use = sorted(u["created_at"] for u in usage
                               if u["item_id"] == item["id"] and u["created_at"] > since)
            later_cites = sorted(c["created_at"] for c in chats
                                 if item["id"] in split(c["cites"]) and c["created_at"] > since)
            first_use = min(later_use[:1] + later_cites[:1], default=None)
            if first_use:
                replacement = item["replaced_by_id"] or next(
                    (i["id"] for i in items.values() if i["supersedes_id"] == item["id"]), None)
                label = f"v{items[replacement]['version']}" if replacement in items else "a newer item"
                reasons.append({
                    "kind": "still_in_use", "since": first_use, "replacement": replacement,
                    "text": (f"Replaced by {label} on {since}, but still used "
                             f"{len(later_use) + len(later_cites)} times since."),
                })
        if not reasons:
            continue
        found.append({
            "id": fingerprint("outdated", item["id"]),
            "type": "outdated", "country": item["country"], "topic": item["topic"],
            "title": f"Outdated: {item['title']}" + (f" v{item['version']}" if item["version"] > 1 else ""),
            "explanation": " ".join(r["text"] for r in reasons),
            "details": {"reasons": reasons},
            "item_ids": [item["id"]],
            "arose_at": min(r["since"] for r in reasons),
        })
    return found


def _topic_for(text: str, topics: list[dict]) -> str:
    words = set(tokens(text))
    best = max(topics, key=lambda t: len(words & set(tokens(t["keywords"]))))
    return best["id"]


def _cluster_label(questions: list[dict], tfidf: TfIdf) -> str:
    weights: Counter[str] = Counter()
    for q in questions:
        for term, w in tfidf.vectorize(q["text"]).items():
            weights[term] += w
    need = max(2, math.ceil(len(questions) * 0.6))
    shared = {t for t, _ in weights.most_common(12)
              if sum(t in tokens(q["text"]) for q in questions) >= need}
    # Build the label from phrases people actually write ("time credit", "notice period"):
    # maximal runs of shared words, preferring multi-word phrases that recur.
    phrases: Counter[str] = Counter()
    for q in questions:
        run: list[str] = []
        for word in re.findall(r"[a-z0-9]+", q["text"].lower()) + [""]:
            stem = tokens(word)
            if stem and stem[0] in shared:
                run.append(word)
                continue
            if run:
                phrases[" ".join(run)] += 1
            run = []
    ranked = sorted(phrases, key=lambda p: (len(p.split()) > 1, phrases[p], len(p)), reverse=True)
    chosen: list[str] = []
    used: set[str] = set()
    for phrase in ranked:
        stems = set(tokens(phrase))
        if stems & used:
            continue
        if chosen and len(phrase.split()) == 1:
            break
        chosen.append(phrase)
        used |= stems
        if len(chosen) == 2:
            break
    return " · ".join(chosen) or tokens(questions[0]["text"])[0]


def detect_missing(ctx: Snapshot) -> list[dict]:
    items, topics = ctx.items, ctx.topics
    active = _active(items)
    questions = [c for c in ctx.chats if "?" in c["text"] and not split(c["cites"])]
    if not questions:
        return []
    tfidf = TfIdf([i["body"] for i in active] + [q["text"] for q in questions])
    item_vecs = {i["id"]: tfidf.vectorize(i["title"] + " " + i["body"]) for i in active}

    unanswered = []
    for q in questions:
        qv = tfidf.vectorize(q["text"])
        same = [(cosine(qv, item_vecs[i["id"]]), i) for i in active if i["country"] == q["country"]]
        other = [(cosine(qv, item_vecs[i["id"]]), i) for i in active if i["country"] != q["country"]]
        best_same = max(same, key=lambda p: p[0], default=(0.0, None))
        best_other = max(other, key=lambda p: p[0], default=(0.0, None))
        # A question is covered in the wrong country when another country's policy
        # clearly matches and the local knowledge is much weaker.
        elsewhere = best_other[0] >= ELSEWHERE_THRESHOLD and best_other[0] > 2 * best_same[0]
        if best_same[0] >= ANSWERED_THRESHOLD and not elsewhere:
            continue
        unanswered.append({**q, "vec": qv, "elsewhere": best_other[1] if elsewhere else None})

    clusters: list[list[dict]] = []
    for q in unanswered:
        for cluster in clusters:
            if cluster[0]["country"] == q["country"] and max(
                    cosine(q["vec"], m["vec"]) for m in cluster) >= QUESTION_CLUSTER_THRESHOLD:
                cluster.append(q)
                break
        else:
            clusters.append([q])

    found = []
    for cluster in clusters:
        if len(cluster) < MIN_QUESTIONS_FOR_GAP:
            continue
        cluster.sort(key=lambda q: q["created_at"])
        country = cluster[0]["country"]
        label = _cluster_label(cluster, tfidf)
        spoken = label.replace(" · ", " and ")
        refs = Counter(q["elsewhere"]["id"] for q in cluster if q["elsewhere"])
        reference = items[refs.most_common(1)[0][0]] if refs and refs.most_common(1)[0][1] * 2 >= len(cluster) else None
        topic = reference["topic"] if reference else _topic_for(" ".join(q["text"] for q in cluster), topics)
        if reference:
            explanation = (f"{len(cluster)} questions from {country} consultants about {spoken}. "
                           f"Only a {reference['country']} policy exists (“{reference['title']}”), "
                           f"so people either guess or apply the wrong country's rules.")
        else:
            explanation = (f"{len(cluster)} questions about {spoken} since {cluster[0]['created_at']}, "
                           f"and no active knowledge item answers them. The answer lives in people's heads.")
        found.append({
            "id": fingerprint("missing", country, *sorted(q["id"] for q in cluster[:2])),
            "type": "missing", "country": country, "topic": topic,
            "title": f"Knowledge gap: {label}" + (f" ({country})" if reference else ""),
            "explanation": explanation,
            "details": {
                "label": label,
                "reference_item": reference["id"] if reference else None,
                "questions": [{"id": q["id"], "text": q["text"], "created_at": q["created_at"],
                               "author_id": q["author_id"], "client_id": q["client_id"]} for q in cluster],
            },
            "item_ids": [],
            "arose_at": cluster[1]["created_at"],
        })
    return found


# --------------------------------------------------------------------------- engine
def pressure(issue: dict, ctx: Snapshot, window_days: int) -> dict:
    start = (ctx.as_of - timedelta(days=window_days)).isoformat()
    if issue["type"] == "missing":
        qs = issue["details"]["questions"]
        uses = len(qs)
        clients = len({q["client_id"] for q in qs if q["client_id"]})
        people = len({q["author_id"] for q in qs})
    else:
        ids = set(issue["item_ids"])
        events = [u for u in ctx.usage if u["item_id"] in ids and u["created_at"] >= start]
        uses = len(events)
        clients = len({u["client_id"] for u in events})
        people = len({u["user_id"] for u in events})
    raw = (uses + 4 * clients + 2 * people) * TYPE_WEIGHT[issue["type"]]
    return {"uses": uses, "clients": clients, "people": people,
            "pressure": round(100 * (1 - math.exp(-raw / 45)))}


class DetectionEngine:
    def __init__(self, judge: ConflictJudge, pressure_window_days: int = 90):
        self.judge = judge
        self.window = pressure_window_days

    @property
    def name(self) -> str:
        return self.judge.name

    def detect(self, snapshot: Snapshot) -> list[dict]:
        candidates = (detect_conflicts(snapshot, self.judge) + detect_duplicates(snapshot)
                      + detect_outdated(snapshot) + detect_missing(snapshot))
        for c in candidates:
            c.update(pressure(c, snapshot, self.window))
        return candidates

    @staticmethod
    def summarize(candidates: list[dict]) -> dict:
        counts = Counter(c["type"] for c in candidates)
        return {"open": len(candidates), **{k: counts.get(k, 0) for k in TYPE_WEIGHT}}
