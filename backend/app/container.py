"""Composition root: the only place that chooses concrete implementations.

Everything else receives its collaborators through constructors (services) or FastAPI
dependencies (API). To swap an implementation, e.g. a Redis login throttle or a
different token format, change it here; nothing else changes. Tests build their own
Container with fakes and pass it to `create_app`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .detection import DetectionEngine
from .judges import CatalogClaimExtractor, GeminiClaimExtractor, GeminiConflictJudge, RuleConflictJudge
from .ports import ClaimExtractor, Clock, ConflictJudge, LoginThrottle, PasswordHasher, RateLimiter, TokenService
from .repositories import UnitOfWork
from .security import (AccessPolicy, AuthService, HmacTokenService, InMemoryLoginThrottle, Pbkdf2PasswordHasher,
                       SlidingWindowRateLimiter)
from .services import DetectionService, DirectoryService, IssueService, ItemService, utc_now
from .settings import Settings, load_settings

log = logging.getLogger("faultlines.container")


@dataclass
class Container:
    settings: Settings
    hasher: PasswordHasher
    tokens: TokenService
    throttle: LoginThrottle
    rate_limiter: RateLimiter
    policy: AccessPolicy
    judge: ConflictJudge
    extractor: ClaimExtractor
    clock: Clock = utc_now
    # built from the above in __post_init__
    auth: AuthService = field(init=False)
    engine: DetectionEngine = field(init=False)
    detection: DetectionService = field(init=False)
    issues: IssueService = field(init=False)
    items: ItemService = field(init=False)
    directory: DirectoryService = field(init=False)

    def __post_init__(self) -> None:
        self.auth = AuthService(self.hasher, self.tokens, self.throttle, self.clock,
                                csrf_key=self.settings.secret_key,
                                session_ttl_seconds=self.settings.session_ttl_seconds)
        self.engine = DetectionEngine(self.judge, self.settings.pressure_window_days)
        self.detection = DetectionService(self.engine, self.clock)
        self.issues = IssueService(self.policy, self.detection, self.clock)
        self.items = ItemService(self.policy)
        self.directory = DirectoryService()

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.settings.db_path)


def _detection_components(settings: Settings) -> tuple[ConflictJudge, ClaimExtractor]:
    rules = (RuleConflictJudge(), CatalogClaimExtractor())
    if settings.detection_engine != "gemini":
        return rules
    try:
        from .llm import GeminiClient
        client = GeminiClient(settings.gcp_project, settings.gcp_location, settings.gemini_model)
    except Exception as exc:  # noqa: BLE001 - misconfiguration means "use rules"
        log.warning("Gemini unavailable, using rules engine: %s", exc)
        return rules
    return GeminiConflictJudge(client, fallback=rules[0]), GeminiClaimExtractor(client, fallback=rules[1])


def build_container(settings: Settings | None = None, **overrides) -> Container:
    """Wire the production implementations. Keyword overrides replace any component."""
    settings = settings or load_settings()
    judge, extractor = _detection_components(settings)
    components = dict(
        settings=settings,
        hasher=Pbkdf2PasswordHasher(settings.password_iterations),
        tokens=HmacTokenService(settings.secret_key, settings.session_ttl_seconds),
        throttle=InMemoryLoginThrottle(settings.login_max_failures, settings.login_window_seconds),
        rate_limiter=SlidingWindowRateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds),
        policy=AccessPolicy(),
        judge=judge,
        extractor=extractor,
    )
    components.update(overrides)
    return Container(**components)
