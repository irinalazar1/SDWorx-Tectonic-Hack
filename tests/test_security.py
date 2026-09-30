"""Access-control checks: the things a security audit looks for (auth, authorization, IDOR)."""
import pytest
from fastapi.testclient import TestClient

from backend.app import seed
from backend.app.main import app


@pytest.fixture(scope="module")
def client():
    seed.build(password="test-password", verbose=False)
    return TestClient(app)


def login(client, user, password="test-password"):
    return client.post("/api/login", json={"username": user, "password": password})


def auth(client, user):
    return {"Authorization": f"Bearer {login(client, user).json()['token']}"}


def issue_ids(client, user, country):
    return [i["id"] for i in client.get("/api/map", headers=auth(client, user)).json()["issues"]
            if i["country"] == country]


def test_api_requires_login(client):
    for path in ("/api/map", "/api/me", "/api/items/KB-BE-101"):
        assert client.get(path).status_code == 401
    assert client.get("/api/map", headers={"Authorization": "Bearer forged.token"}).status_code == 401


def test_wrong_password_and_departed_user_rejected(client):
    assert login(client, "u-tom", "nope").status_code == 401
    assert login(client, "u-marc").status_code == 401  # left the company


def test_scope_isolation_idor(client):
    nl_issue = issue_ids(client, "u-sofie", "NL")[0]
    tom = auth(client, "u-tom")  # BE only
    assert all(i["country"] == "BE" for i in client.get("/api/map", headers=tom).json()["issues"])
    assert client.get(f"/api/issues/{nl_issue}", headers=tom).status_code == 404
    assert client.get("/api/items/KB-NL-210", headers=tom).status_code == 404


def test_consultant_cannot_resolve(client):
    be_issue = issue_ids(client, "u-an", "BE")[0]
    res = client.post(f"/api/issues/{be_issue}/resolve", headers=auth(client, "u-tom"),
                      json={"action": "accept", "note": "x"})
    assert res.status_code == 403


def test_owner_cannot_resolve_other_country(client):
    nl_issue = issue_ids(client, "u-sofie", "NL")[0]
    res = client.post(f"/api/issues/{nl_issue}/resolve", headers=auth(client, "u-an"),
                      json={"action": "accept", "note": "x"})
    assert res.status_code == 404


def test_keep_must_reference_issue_item(client):
    conflict = next(i for i in client.get("/api/map", headers=auth(client, "u-sofie")).json()["issues"]
                    if i["type"] == "conflict" and i["country"] == "NL")
    res = client.post(f"/api/issues/{conflict['id']}/resolve", headers=auth(client, "u-jeroen"),
                      json={"action": "keep", "keep_item_id": "KB-BE-101"})
    assert res.status_code == 400


def test_invalid_action_rejected(client):
    be_issue = issue_ids(client, "u-an", "BE")[0]
    res = client.post(f"/api/issues/{be_issue}/resolve", headers=auth(client, "u-an"),
                      json={"action": "drop_table"})
    assert res.status_code == 422


def test_login_throttled(client):
    for _ in range(5):
        login(client, "u-noah", "wrong")
    assert login(client, "u-noah").status_code == 429


def test_security_headers(client):
    res = client.get("/")
    assert "default-src 'self'" in res.headers["Content-Security-Policy"]
    assert res.headers["X-Frame-Options"] == "DENY"
