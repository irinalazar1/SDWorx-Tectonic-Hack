"""The answer key: every problem seeded into /data must be found, and nothing else."""
import pytest
from fastapi.testclient import TestClient

from backend.app import db, seed
from backend.app.main import app

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


@pytest.fixture()
def fresh():
    seed.build(password="test-password", verbose=False)
    yield
    db.connect().close()


def issues():
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM issues")]


def test_answer_key(fresh):
    found = {(i["type"], i["country"], tuple(sorted(db.split(i["item_ids"])))) for i in issues()}
    assert found == EXPECTED


def test_gap_labels_and_scope_gap(fresh):
    gaps = {i["country"]: i for i in issues() if i["type"] == "missing"}
    assert "time credit" in gaps["BE"]["title"] and "notice period" in gaps["BE"]["title"]
    assert "remote work allowance" in gaps["NL"]["title"]
    assert "KB-BE-130" in gaps["NL"]["details"]


def test_highest_pressure_is_the_parental_leave_conflict(fresh):
    top = max(issues(), key=lambda i: i["pressure"])
    assert top["type"] == "conflict" and "parental leave" in top["title"]


def login(client, user):
    res = client.post("/api/login", json={"username": user, "password": "test-password"})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_resolving_conflict_cascades(fresh):
    client = TestClient(app)
    headers = login(client, "u-an")
    conflict = next(i for i in client.get("/api/map", headers=headers).json()["issues"]
                    if i["type"] == "conflict")
    res = client.post(f"/api/issues/{conflict['id']}/resolve", headers=headers,
                      json={"action": "keep", "keep_item_id": "KB-BE-101"})
    assert res.status_code == 200
    states = {(i["type"], tuple(db.split(i["item_ids"]))): i["state"] for i in issues()}
    assert states[("conflict", ("KB-BE-044", "KB-BE-101"))] == "resolved"
    # The old FAQ is now deprecated, so its "outdated" issue clears automatically.
    assert states[("outdated", ("KB-BE-044",))] == "resolved"
    item = client.get("/api/items/KB-BE-044", headers=headers).json()
    assert item["state"] == "deprecated" and item["replaced_by"]["id"] == "KB-BE-101"
