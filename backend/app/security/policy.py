"""Authorization rules in one place.

Every service asks the policy instead of checking roles or scopes itself, so the rules
are consistent everywhere and can be tested without HTTP or a database.
"""
from __future__ import annotations

from typing import Iterable

from ..domain import Forbidden, NotFound, User


class AccessPolicy:
    def can_view(self, user: User, country: str) -> bool:
        return country in user.scopes

    def ensure_can_view(self, user: User, country: str, what: str = "Resource") -> None:
        # Out-of-scope answers exactly like "missing", so ids cannot be probed (IDOR).
        if not self.can_view(user, country):
            raise NotFound(f"{what} not found")

    def ensure_owner(self, user: User) -> None:
        if not user.is_owner:
            raise Forbidden("Only knowledge owners can do this")

    def ensure_can_resolve(self, user: User, issue_country: str, item_countries: Iterable[str]) -> None:
        self.ensure_owner(user)
        self.ensure_can_view(user, issue_country, "Issue")
        if not all(self.can_view(user, c) for c in item_countries):
            raise Forbidden("You do not own knowledge in this scope")

    def can_resolve(self, user: User, issue_country: str) -> bool:
        return user.is_owner and self.can_view(user, issue_country)
