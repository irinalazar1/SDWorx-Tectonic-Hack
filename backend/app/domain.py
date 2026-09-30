"""Domain objects and errors shared by all layers (no framework or database imports)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str
    team: str
    scopes: tuple[str, ...]
    active: bool = True

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "role": self.role,
                "team": self.team, "scopes": list(self.scopes)}


@dataclass
class Snapshot:
    """Everything the detection engine needs, loaded once. The engine never touches storage."""
    items: dict[str, dict]
    claims: dict[str, list[dict]]
    users: dict[str, dict]
    topics: list[dict]
    chats: list[dict]
    usage: list[dict]
    as_of: date = field(default_factory=date.today)


@dataclass(frozen=True)
class ResolveCommand:
    action: str
    keep_item_id: str | None = None
    note: str | None = None


# --------------------------------------------------------------------------- errors
class DomainError(Exception):
    """Base class; the API layer maps each subclass to an HTTP status."""


class NotFound(DomainError):
    pass


class Forbidden(DomainError):
    pass


class InvalidAction(DomainError):
    pass


class Conflict(DomainError):
    """The resource changed in the meantime (e.g. someone else resolved the issue first)."""


class AuthenticationFailed(DomainError):
    pass


class TooManyAttempts(DomainError):
    pass


def split(value: str | None) -> list[str]:
    return [v for v in (value or "").split(",") if v]
