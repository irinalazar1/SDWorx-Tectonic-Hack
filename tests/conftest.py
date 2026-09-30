import dataclasses
import os
import secrets
import tempfile

import pytest

# Point everything at a throwaway database before any app module is imported.
os.environ["FAULTLINES_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DETECTION_ENGINE"] = "rules"
os.environ["DEMO_PASSWORD"] = secrets.token_urlsafe(16)  # generated, never hardcoded
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import seed  # noqa: E402
from backend.app.container import build_container  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.app.settings import load_settings  # noqa: E402

PASSWORD = os.environ["DEMO_PASSWORD"]


def test_settings(**changes):
    # TestClient talks plain http, so the cookie cannot be Secure in tests.
    base = dataclasses.replace(load_settings(env_file=None), cookie_secure=False, demo_mode=True,
                               password_iterations=1_000, rate_limit_requests=100_000)
    return dataclasses.replace(base, **changes)


test_settings.__test__ = False  # not a test, despite the name


@pytest.fixture()
def container():
    """A fresh container and freshly seeded database for each test."""
    c = build_container(test_settings())
    seed.build(password=PASSWORD, verbose=False, container=c)
    return c


@pytest.fixture()
def app(container):
    return create_app(container)


@pytest.fixture()
def client(app):
    """An anonymous client."""
    return TestClient(app)


@pytest.fixture()
def as_user(app):
    """Factory: a client signed in as `user`, sending its CSRF token on every request."""
    def make(user: str) -> TestClient:
        c = TestClient(app)
        res = c.post("/api/login", json={"username": user, "password": PASSWORD})
        assert res.status_code == 200, res.text
        c.headers["X-CSRF-Token"] = res.json()["csrf_token"]
        return c
    return make


def login(client, user, password=PASSWORD):
    return client.post("/api/login", json={"username": user, "password": password})
