"""Security tests through the HTTP API, grouped by what Aikido's AI Code Audit checks:
authentication, authorization, IDOR and business-logic flaws, plus hardening."""
import pytest
from fastapi.testclient import TestClient

from backend.app.container import build_container
from backend.app.main import create_app
from tests.conftest import PASSWORD, login, test_settings


def issue_of(c, type_=None, country=None):
    return next(i for i in c.get("/api/map").json()["issues"]
                if (type_ is None or i["type"] == type_) and (country is None or i["country"] == country))


# ======================================================================== authentication
def test_api_requires_session(client):
    for path in ("/api/map", "/api/me", "/api/items/KB-BE-101"):
        assert client.get(path).status_code == 401


def test_forged_or_tampered_cookie_rejected(client, as_user):
    token = as_user("u-an").cookies.get("fl_session")
    body, sig = token.split(".")
    for bad in ("forged.token", f"{body}.{sig[:-2]}AA", f"{body[:-2]}xx.{sig}", "x" * 5000):
        client.cookies.set("fl_session", bad)
        assert client.get("/api/map").status_code == 401


def test_session_cookie_is_httponly_and_strict(client):
    res = login(client, "u-an")
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "token" not in res.json()  # the session token is never exposed to scripts


def test_secure_cookie_by_default(container):
    app = create_app(build_container(test_settings(cookie_secure=True)))
    res = TestClient(app, base_url="https://testserver").post(
        "/api/login", json={"username": "u-an", "password": PASSWORD})
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("__Host-fl_session=") and "Secure" in cookie


def test_wrong_password_and_departed_user_rejected(client):
    assert login(client, "u-tom", "not-the-password").status_code == 401
    assert login(client, "u-marc").status_code == 401  # left the company


def test_same_error_for_unknown_and_known_users(client):
    a = login(client, "u-tom", "not-the-password")
    b = login(client, "nobody-here", "not-the-password")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_logout_revokes_session_server_side(as_user):
    an = as_user("u-an")
    stolen = an.cookies.get("fl_session")
    assert an.post("/api/logout", json={}).status_code == 200
    replay = TestClient(an.app)
    replay.cookies.set("fl_session", stolen)
    assert replay.get("/api/map").status_code == 401


def test_deactivated_user_loses_access_immediately(container, as_user):
    an = as_user("u-an")
    with container.unit_of_work() as uow:
        uow.conn.execute("UPDATE users SET active=0 WHERE id='u-an'")
    assert an.get("/api/map").status_code == 401


def test_login_throttled(client):
    for _ in range(5):
        login(client, "u-noah", "not-the-password")
    assert login(client, "u-noah").status_code == 429


def test_auth_events_are_audited(container, client):
    login(client, "u-tom", "not-the-password")
    login(client, "u-tom")
    with container.unit_of_work() as uow:
        actions = [r["action"] for r in uow.conn.execute("SELECT action FROM audit_log WHERE action LIKE 'auth.%'")]
    assert "auth.failed" in actions and "auth.login" in actions


def test_demo_accounts_hidden_unless_demo_mode(container):
    app = create_app(build_container(test_settings(demo_mode=False)))
    assert TestClient(app).get("/api/demo-users").status_code == 404


# ======================================================================== CSRF
def test_state_change_requires_csrf_token(as_user):
    an = as_user("u-an")
    issue = issue_of(an, "missing", "BE")
    del an.headers["X-CSRF-Token"]
    assert an.post(f"/api/issues/{issue['id']}/resolve", json={"action": "assign"}).status_code == 403
    an.headers["X-CSRF-Token"] = "0" * 64
    assert an.post(f"/api/issues/{issue['id']}/resolve", json={"action": "assign"}).status_code == 403


def test_csrf_token_is_bound_to_session(as_user):
    an, pieter = as_user("u-an"), as_user("u-pieter")
    an.headers["X-CSRF-Token"] = pieter.headers["X-CSRF-Token"]
    issue = issue_of(an, "missing", "BE")
    assert an.post(f"/api/issues/{issue['id']}/resolve", json={"action": "assign"}).status_code == 403


def test_non_json_writes_rejected(as_user):
    an = as_user("u-an")
    res = an.post("/api/logout", content="x=1", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert res.status_code == 415


# ======================================================================== authorization + IDOR
def test_scope_isolation(as_user, container):
    sofie, tom = as_user("u-sofie"), as_user("u-tom")
    nl_issue = issue_of(sofie, country="NL")
    assert all(i["country"] == "BE" for i in tom.get("/api/map").json()["issues"])
    assert all(i["country"] == "BE" for i in tom.get("/api/map").json()["items"])
    assert tom.get(f"/api/issues/{nl_issue['id']}").status_code == 404
    assert tom.get("/api/items/KB-NL-210").status_code == 404


def test_out_of_scope_ids_look_missing(as_user):
    tom = as_user("u-tom")
    real_nl = tom.get("/api/items/KB-NL-210")
    nonexistent = tom.get("/api/items/KB-NL-999")
    assert real_nl.status_code == nonexistent.status_code == 404 and real_nl.json() == nonexistent.json()


def test_no_cross_scope_ids_in_responses(as_user):
    """The NL gap points at a BE policy; an NL-only owner must not receive that id anywhere."""
    jeroen = as_user("u-jeroen")
    assert "KB-BE" not in jeroen.get("/api/map").text
    gap = issue_of(jeroen, "missing", "NL")
    assert "KB-BE" not in jeroen.get(f"/api/issues/{gap['id']}").text


def test_consultant_cannot_resolve(as_user):
    tom = as_user("u-tom")
    issue = issue_of(tom, country="BE")
    assert tom.post(f"/api/issues/{issue['id']}/resolve",
                    json={"action": "accept", "note": "a long enough note"}).status_code == 403


def test_owner_cannot_resolve_other_country(as_user):
    nl_issue = issue_of(as_user("u-sofie"), country="NL")
    res = as_user("u-an").post(f"/api/issues/{nl_issue['id']}/resolve",
                               json={"action": "accept", "note": "a long enough note"})
    assert res.status_code == 404


def test_global_detection_endpoint_removed(as_user):
    assert as_user("u-an").post("/api/detect", json={}).status_code in (404, 405)


# ======================================================================== business logic
def test_keep_must_reference_issue_item(as_user):
    jeroen = as_user("u-jeroen")
    conflict = issue_of(jeroen, "conflict", "NL")
    res = jeroen.post(f"/api/issues/{conflict['id']}/resolve", json={"action": "keep", "keep_item_id": "KB-BE-101"})
    assert res.status_code == 400


def test_cannot_resolve_twice(as_user):
    an = as_user("u-an")
    conflict = issue_of(an, "conflict", "BE")
    url = f"/api/issues/{conflict['id']}/resolve"
    assert an.post(url, json={"action": "keep", "keep_item_id": "KB-BE-101"}).status_code == 200
    assert an.post(url, json={"action": "keep", "keep_item_id": "KB-BE-044"}).status_code == 409


def test_cannot_steal_an_assignment(as_user):
    an, pieter = as_user("u-an"), as_user("u-pieter")
    gap = issue_of(an, "missing", "BE")
    url = f"/api/issues/{gap['id']}/resolve"
    assert an.post(url, json={"action": "assign"}).status_code == 200
    assert pieter.post(url, json={"action": "assign"}).status_code == 409
    assert an.post(url, json={"action": "assign"}).status_code == 200  # re-assigning to yourself is fine


def test_accept_requires_a_real_reason(as_user):
    an = as_user("u-an")
    dup = issue_of(an, "duplicate", "BE")
    assert an.post(f"/api/issues/{dup['id']}/resolve", json={"action": "accept", "note": "ok"}).status_code == 400


def test_action_must_fit_issue_type(as_user):
    an = as_user("u-an")
    gap = issue_of(an, "missing", "BE")
    assert an.post(f"/api/issues/{gap['id']}/resolve", json={"action": "retire"}).status_code == 400


# ======================================================================== input validation + hardening
@pytest.mark.parametrize("path", ["/api/issues/../../etc", "/api/issues/con-XYZ", "/api/items/KB-BE-101;drop"])
def test_malformed_ids_rejected(as_user, path):
    assert as_user("u-an").get(path).status_code in (404, 422)


def test_invalid_action_and_username_rejected(as_user, client):
    an = as_user("u-an")
    issue = issue_of(an, country="BE")
    assert an.post(f"/api/issues/{issue['id']}/resolve", json={"action": "drop_table"}).status_code == 422
    assert client.post("/api/login", json={"username": "a' OR '1'='1", "password": "x"}).status_code == 422


def test_oversized_body_rejected(as_user):
    an = as_user("u-an")
    issue = issue_of(an, country="BE")
    res = an.post(f"/api/issues/{issue['id']}/resolve", json={"action": "accept", "note": "x" * 20_000})
    assert res.status_code in (413, 422)


def test_rate_limit(container):
    app = create_app(build_container(test_settings(rate_limit_requests=3)))
    c = TestClient(app)
    codes = [c.get("/api/me").status_code for _ in range(4)]
    assert codes[-1] == 429


def test_security_headers(client):
    for res in (client.get("/"), client.get("/api/me")):
        assert "default-src 'self'" in res.headers["Content-Security-Policy"]
        assert res.headers["X-Frame-Options"] == "DENY"
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert "uvicorn" not in res.headers.get("server", "")
    assert client.get("/api/me").headers["Cache-Control"] == "no-store"


def test_no_api_docs_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_robots_blocks_all_crawlers(client):
    res = client.get("/robots.txt")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    lines = [l.strip() for l in res.text.splitlines() if l.strip() and not l.startswith("#")]
    assert lines == ["User-agent: *", "Disallow: /"]
    for path in ("/", "/api/me"):
        assert client.get(path).headers["X-Robots-Tag"] == "noindex, nofollow"
