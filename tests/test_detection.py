"""The answer key: every problem seeded into /data must be found, and nothing else."""

EXPECTED = {
    ("conflict", "BE", ("KB-BE-044", "KB-BE-101")),
    ("conflict", "NL", ("KB-NL-210", "KB-NL-211")),
    ("duplicate", "BE", ("KB-BE-110", "KB-BE-310")),
    ("outdated", "BE", ("KB-BE-044",)),   # owner left + review overdue
    ("outdated", "BE", ("KB-BE-120",)),   # superseded but still in use
    ("outdated", "BE", ("KB-BE-130",)),   # review overdue
    ("missing", "BE", ()),                # time credit / notice period
    ("missing", "NL", ()),                # remote work allowance: BE only
}


def issues(container):
    with container.unit_of_work() as uow:
        return uow.issues.all()


def test_answer_key(container):
    found = {(i["type"], i["country"], tuple(sorted(i["item_ids"]))) for i in issues(container)}
    assert found == EXPECTED


def test_gap_labels_and_scope_gap(container):
    gaps = {i["country"]: i for i in issues(container) if i["type"] == "missing"}
    assert "time credit" in gaps["BE"]["title"] and "notice period" in gaps["BE"]["title"]
    assert "remote work allowance" in gaps["NL"]["title"]
    assert gaps["NL"]["details"]["reference_item"] == "KB-BE-130"


def test_highest_pressure_is_the_parental_leave_conflict(container):
    top = max(issues(container), key=lambda i: i["pressure"])
    assert top["type"] == "conflict" and "parental leave" in top["title"]


def test_resolving_conflict_cascades(container, as_user):
    an = as_user("u-an")
    conflict = next(i for i in an.get("/api/map").json()["issues"] if i["type"] == "conflict")
    res = an.post(f"/api/issues/{conflict['id']}/resolve", json={"action": "keep", "keep_item_id": "KB-BE-101"})
    assert res.status_code == 200
    states = {(i["type"], tuple(i["item_ids"])): i["state"] for i in issues(container)}
    assert states[("conflict", ("KB-BE-044", "KB-BE-101"))] == "resolved"
    # The old FAQ is now deprecated, so its "outdated" issue clears automatically.
    assert states[("outdated", ("KB-BE-044",))] == "resolved"
    item = an.get("/api/items/KB-BE-044").json()
    assert item["state"] == "deprecated" and item["replaced_by"]["id"] == "KB-BE-101"
