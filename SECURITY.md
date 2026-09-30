# Security

This document maps Fault Lines' controls to the four areas Aikido's AI Code Audit checks. Every control listed here has an automated test in `tests/test_security.py` or `tests/test_di.py`.

## Architecture

Security components are small classes behind interfaces (`backend/app/ports.py`), wired in one place (`backend/app/container.py`) and injected into services and routes through FastAPI dependencies (`backend/app/api/deps.py`). Authorization rules live in one class, `AccessPolicy`, which every service asks instead of checking roles itself.

## Authentication

| Control | Where |
|---|---|
| Passwords hashed with PBKDF2-SHA256, 600,000 iterations, random salt, constant-time compare | `security/hashing.py` |
| Server-side sessions: the cookie holds a signed token with a random session id; only a SHA-256 of that id is stored | `security/auth.py`, `repositories.SessionRepository` |
| Logout revokes the session on the server; a copied cookie stops working immediately | `AuthService.logout` |
| Deactivated users lose access on their next request, and their session is revoked | `AuthService.authenticate` |
| Session cookie is `HttpOnly`, `SameSite=Strict`, `Secure` with the `__Host-` prefix by default; the token is never exposed to JavaScript or stored in browser storage | `api/routes.py` |
| Sessions expire (8 h default); old sessions are cleaned up | `settings.py`, `SessionRepository` |
| Unknown usernames cost the same password check as known ones, with an identical error, so accounts cannot be discovered by timing or messages | `AuthService.login` |
| Login throttling per username and per client address | `security/throttle.py` |
| Sign-ins, failures, throttling and sign-outs are written to the audit log, including for failed requests | `AuthService` |
| `SECRET_KEY` must be at least 32 characters; demo passwords at least 10 | `settings.py`, `seed.py` |
| Demo account list only exists when `DEMO_MODE=true` | `api/routes.py` |

## Authorization and IDOR

| Control | Where |
|---|---|
| Every read is limited to the user's country scopes on the server | `IssueService`, `ItemService`, `AccessPolicy` |
| Out-of-scope ids return the same 404 as ids that do not exist, so they cannot be probed | `AccessPolicy.ensure_can_view` |
| Responses never contain ids from other scopes (e.g. a gap that points at another country's policy is scrubbed) | `IssueService._scrub` |
| Only knowledge owners can resolve, only in their own scope, and only using items that belong to the issue | `AccessPolicy.ensure_can_resolve`, `require_owner` |
| Every state-changing request needs a CSRF token bound to the session (keyed HMAC of the session id) | `require_csrf` |
| No endpoint lets a single-scope user trigger changes across other scopes | the former global `/api/detect` was removed |

## Business logic

| Control | Where |
|---|---|
| Resolving takes the database write lock first and claims the issue with a conditional update before changing any document, so two owners cannot both resolve it (the loser gets 409) | `IssueService.resolve`, `IssueRepository.close_if_open` |
| An issue that is already closed cannot be resolved again | `IssueService.resolve` |
| Assignments cannot be taken over by another owner | `IssueRepository.assign_if_available` |
| Only an active document can be kept as the source of truth, and only documents in the issue can be kept | `IssueService.resolve` |
| Each issue type only accepts the actions that make sense for it | `services.ACTIONS` |
| Accepting an issue requires a written reason of at least 10 characters, stored in the audit trail | `IssueService.resolve` |
| Every lifecycle change is recorded in the audit log | `AuditRepository` |

## Injection and input handling

| Control | Where |
|---|---|
| Every SQL statement is a constant string with bound parameters; lists are passed as one JSON parameter to SQLite's `json_each` | `repositories.py` |
| Path ids, usernames, actions and item ids are validated against strict patterns before reaching a service | `api/routes.py` |
| Only `application/json` bodies are accepted on writes; bodies are capped at 16 KB | `main.py` |
| The frontend inserts all data as text (no `innerHTML`); a strict Content-Security-Policy allows scripts only from the app itself | `frontend/app.js`, `main.py` |

## Hardening

- Security headers on every response: CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP/CORP, and HSTS over HTTPS.
- `Cache-Control: no-store` on all API responses.
- Kept out of search engines: `robots.txt` disallows all crawlers, and every response carries `X-Robots-Tag: noindex, nofollow` for crawlers that ignore robots.txt. robots.txt lists no paths, so it reveals nothing about the app.
- Rate limiting per client address on `/api`.
- API docs (`/docs`, `/openapi.json`) are disabled.
- `python -m backend.app.serve` binds to 127.0.0.1, hides the server banner and ignores `X-Forwarded-*` unless `TRUST_PROXY=true`.
- Database errors return a generic message; details stay in the server log.
- Dependencies are pinned to exact versions; test tooling is separate (`requirements-dev.txt`).
- No secrets in the repository: `.env` is git-ignored, and tests generate random passwords and keys.

## Known limitations

- Login throttling and rate limiting are in memory, so they reset on restart and are per process. A shared store (e.g. Redis) can implement the same ports for multi-instance deployments.
- Per-username throttling means someone can temporarily lock an account by failing its login on purpose (5 attempts, 5 minutes). This trades a short lockout for brute-force protection.
- This is a hackathon demo with synthetic data. Production would add single sign-on, TLS termination, and central log shipping for the audit trail.

## Reporting

Found something? Open an issue on the repository or contact the team directly.
