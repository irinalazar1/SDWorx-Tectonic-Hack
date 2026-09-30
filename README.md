# Fault Lines 🌋

**See where your organisation's knowledge is cracking, before someone falls through.**

Fault Lines is a seismic map of an organisation's knowledge. It scans policies, versions, owners, usage and the questions people ask, and makes weak spots visible: conflicting answers, duplicated content, outdated documents and knowledge gaps. It ranks them by how much they actually hurt, and lets knowledge owners fix them at the source.

Built at the **Tectonic Hackathon (30 Sept 2026)** for the **SD Worx challenge "Unlock the Knowledge Within"**, inspiration area **Detect**: *how might conflicting, duplicated, missing or outdated knowledge become visible?*

---

## The problem

A payroll consultant gets an urgent client question. Search returns three documents: one updated last month, one whose owner left the company, and one that may be for another country. A colleague in Teams says something else again. The information exists, but the consultant can't tell which answer to trust.

Search and AI summaries make this worse, not better: they find *more* sources and blend them into one confident answer. The real problem isn't finding knowledge. It's that **nobody can see which knowledge is broken**.

## Our answer

Fault Lines doesn't answer questions. It **makes the reliability of knowledge visible**.

We borrowed an idea from telecom service management: treat every piece of knowledge like a product specification. Each item is **versioned, owned, scoped** (country, topic) and moves through a **lifecycle** (`draft → active → deprecated → retired`). That turns "is this still correct?" from a guess into something a machine can check.

| On the map | Problem | How it is detected |
|---|---|---|
| 🔴 **Fault line** | Conflict | Two *active* items for the same country make different claims about the same subject |
| 🟡 **Echo** | Duplicate | Near-identical content maintained twice, often by different owners (tomorrow's conflict) |
| 🟤 **Erosion** | Outdated | A replaced version is still being used, the review date has passed, or the owner left the company |
| ⚫ **Crater** | Gap | Questions keep being asked that no active item answers, including "covered for Belgium, missing for the Netherlands" |

Every issue gets a **pressure score (0–100)** from the last 90 days: how often the affected knowledge was used, for how many clients, and by how many colleagues. Owners fix the cracks people are actually falling into first.

Every issue also has **its own lifecycle** (`detected → assigned → resolved / accepted`) with a full audit trail. When an owner keeps one side of a conflict, the other side is deprecated automatically, and every issue that depended on it clears too.

## Features

- **Knowledge health overview**: four counters (conflicts, duplicates, outdated, gaps) that also work as filters.
- **Knowledge map**: six topic areas, each with its documents as nodes (Belgium dark outline, Netherlands blue, size = usage). Issues are drawn on top: red fault lines, dashed amber echoes, dashed erosion rings and dark craters. The contour lines of a topic turn red when an issue there is under high pressure. Hover for details, click to open.
- **Time-lapse**: drag the slider under the map or press ▶ to watch three years of knowledge drift: a policy gets copied, one copy is updated, and a fault line opens.
- **Issue ledger**: every open issue ranked by pressure, with tabs per type and a list of closed issues.
- **Issue detail**: both sides of a conflict next to each other, the exact statements that disagree, owners, review dates, usage per month, affected clients, and a timeline of how the problem formed.
- **Resolve at the source**: keep one version, retire, reconfirm and take over ownership, assign a gap to yourself, or accept it with a written reason.
- **Warnings in context**: open any document and a banner tells you straight away if it is contradicted, outdated, replaced or has no active owner.
- **Roles and scopes**: consultants see their own country, read-only. Knowledge owners resolve issues within their own scope.
- **SD Worx look**: styled after SD Worx's own web design: Inter type, slate text, near-black buttons, blue for selection and 4px corners. Light theme, works on mobile.

## Demo walkthrough (3 minutes)

Sign in as **Sofie Claes** (knowledge owner, BE + NL).

1. **Knowledge health.** The counters show 2 conflicts, 1 duplicate, 3 outdated documents and 2 gaps. On the map you see two red fault lines, one echo, three erosion rings and two craters. At the top of the issue ledger: *Conflicting answers: parental leave holiday accrual* (pressure 77).
2. **Open it.** The old consultant FAQ says parental leave does *not* build up holiday entitlement; the current handbook says it *does*. The FAQ's owner left the company in 2025 and its review is long overdue. Together the two documents were used 25 times for 5 clients in the last 90 days.
3. **Time-lapse.** Press ▶ under the map: the FAQ goes stale in 2025, the new policy lands in January 2026, and the fault line opens.
4. **In context.** Click the FAQ on the map (BE-044): a red banner warns that its owner has left, that it conflicts with another document, and that it is outdated.
5. **Resolve.** In the issue, choose *Keep BE-101 v3, deprecate the other*. The fault line disappears, the FAQ becomes deprecated, and its "outdated" issue closes on its own ("1 related issue cleared too").
6. **The crater.** Open *Knowledge gap: notice period · time credit*. Consultants asked about it five times in a year and no document answers it: the knowledge lives in people's heads. Click *I'll write it (assign to me)*.

## Run it

Requirements: **Python 3.10+**.

```bash
git clone https://github.com/irinalazar1/SDWorx-Tectonic-Hack.git
cd SDWorx-Tectonic-Hack
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in two values:

```dotenv
DEMO_PASSWORD=at-least-10-characters
SECRET_KEY=paste-a-long-random-string   # python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`DEMO_MODE=true` (already set in `.env.example`) lists the demo accounts on the sign-in screen. With it off, you type a username such as `u-sofie`. Turn it off anywhere the app is publicly reachable.

Build the database and start the app:

```bash
python -m backend.app.seed          # builds faultlines.db from /data and runs detection
python -m backend.app.serve         # starts the server on http://localhost:8000
```

Open **http://localhost:8000** in Chrome or Firefox, pick an account and sign in with your `DEMO_PASSWORD`. (The session cookie is `Secure` by default, which Chrome and Firefox allow on localhost. In Safari, set `COOKIE_SECURE=false` in `.env`.)

| Account | Role | Scope |
|---|---|---|
| Sofie Claes | Knowledge owner | BE + NL (best for the demo) |
| An Peeters | Knowledge owner | BE |
| Jeroen de Vries | Knowledge owner | NL |
| Tom Janssens | Consultant (read-only) | BE |
| Lotte Bakker | Consultant (read-only) | NL |

Usernames are the ids in `data/users.json` (e.g. `u-sofie`, `u-an`, `u-tom`). Marc Dubois is in the data as an owner who left the company, so he cannot sign in.

Run `python -m backend.app.seed` again at any time to reset the demo (stop the server first).

### Optional: Gemini on Vertex AI

By default the app uses the offline **rules** engine, so it runs anywhere without API keys. To let Gemini read the documents and judge contradictions:

```bash
pip install -r requirements-gemini.txt
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project YOUR_PROJECT_ID
```

Then set in `.env`:

```dotenv
DETECTION_ENGINE=gemini
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=europe-west1
GEMINI_MODEL=gemini-2.5-flash
```

Re-run the seed; it prints `Detection (gemini)` when Gemini is active. If Gemini is unreachable or misconfigured, it falls back to the rules engine automatically.

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

49 tests in total:

- `tests/test_detection.py` is the **answer key**: every problem deliberately planted in `/data` must be found, and nothing else. It also checks the resolve cascade.
- `tests/test_security.py` covers what Aikido's AI Code Audit checks, through the API: authentication (sessions, logout, cookie flags, throttling, enumeration), CSRF, authorization and IDOR, business logic (double resolve, assignment stealing), input validation and hardening.
- `tests/test_di.py` tests each component in isolation or swapped via injection: the access policy, token expiry and throttling with a fake clock, the auth service with fake collaborators, the detection engine with fake judges, and the whole app with an injected throttle or an overridden current user.

## Architecture

The backend is split into loosely coupled layers. Each layer only talks to the one below it through a small interface, and every concrete implementation is chosen in exactly one place.

```
 frontend (D3 map, drawer, time-lapse)
      │  HTTP / JSON
 ┌────▼──────────────────────────────────────────────────────────┐
 │ API layer        api/routes.py, api/deps.py                   │  HTTP only: validate input,
 │                  FastAPI Depends(...) injects everything      │  call one service, map errors
 ├───────────────────────────────────────────────────────────────┤
 │ Service layer    services.py  +  security/ (auth, policy)     │  use cases, no HTTP, no SQL
 ├──────────────────────────────┬────────────────────────────────┤
 │ Detection engine             │ Data access                    │
 │ detection.py (pure)          │ repositories.py + UnitOfWork   │
 │ judge injected via port      │ all SQL lives here             │
 └──────────────────────────────┴────────────────────────────────┘
      ▲ ports.py: PasswordHasher · TokenService · LoginThrottle · ConflictJudge · ClaimExtractor · Clock
      │
 container.py: the composition root, which wires concrete implementations to the ports
```

**Dependency injection for security.** Every security component sits behind a port and is injected, never imported directly:

| Port | Default implementation | Injected into |
|---|---|---|
| `PasswordHasher` | `Pbkdf2PasswordHasher` (PBKDF2-SHA256) | `AuthService`, seed |
| `TokenService` | `HmacTokenService` (signed, expiring) | `AuthService` |
| `SessionStore` | `SessionRepository` (SQLite, hashed ids) | `AuthService` |
| `LoginThrottle` | `InMemoryLoginThrottle` | `AuthService` |
| `RateLimiter` | `SlidingWindowRateLimiter` | request guard in `main.py` |
| `AccessPolicy` | scope + role rules in one class | every service, `require_owner` |
| `Clock` | UTC now | token service, throttle, services |

Routes declare what they need (`Depends(get_current_user)`, `Depends(require_owner)`, `Depends(get_issue_service)`); the providers in `api/deps.py` resolve them from the container on `app.state`. So:

- **Swapping an implementation** (e.g. a Redis-backed throttle, or a different token format) is one line in `container.py`.
- **Tests inject fakes** without patching: `build_container(settings, throttle=AlwaysBlocked())`, `HmacTokenService(..., clock=FakeClock())`, or `app.dependency_overrides[get_current_user] = ...`.
- **Authorization lives in one place**: services ask `AccessPolicy` instead of checking roles or scopes themselves.

**Detection is pure.** `DetectionEngine` receives a `Snapshot` and an injected `ConflictJudge` and returns candidate issues, with no database or configuration access. `RuleConflictJudge` (offline, default) and `GeminiConflictJudge` (Vertex AI, with automatic rules fallback) are interchangeable.

```
data/*.json ──► seed ──► SQLite catalog ──► Snapshot ──► DetectionEngine(judge)
                                                            │ candidates + pressure
                                              IssueRepository.sync (lifecycle + audit)
```

## Project structure

```
backend/app/
  main.py          app factory: create_app(container), rate limiting, error mapping, security headers
  serve.py         runs the server with safe defaults (localhost, no server banner)
  container.py     composition root: picks and wires all implementations
  settings.py      immutable Settings loaded from the environment
  ports.py         interfaces (Protocols) the core depends on
  domain.py        User, Snapshot, ResolveCommand, domain errors
  api/
    routes.py      HTTP endpoints (thin)
    deps.py        FastAPI dependency providers (injection points)
  services.py      use cases: map, issue detail, resolve, detection run, item detail
  security/
    auth.py        AuthService (login, authenticate), depends only on ports
    policy.py      AccessPolicy: who may view or resolve what
    hashing.py     PBKDF2 password hasher
    tokens.py      HMAC-signed expiring tokens
    throttle.py    login throttling and request rate limiting
  detection.py     pure detection engine: the four detectors + pressure score
  judges.py        rule and Gemini conflict judges / claim extractors
  llm.py           thin Gemini (Vertex AI) client
  repositories.py  all SQL: users, catalog, issues, audit + UnitOfWork
  db.py            schema and connection factory
  seed.py          builds the database from /data
  textsim.py       dependency-free TF-IDF similarity
data/              synthetic payroll knowledge for Belgium and the Netherlands
frontend/
  index.html       page structure
  app.js           map (D3), time-lapse, ledger, issue detail, document viewer
  styles.css       SD Worx-style design tokens and components
  robots.txt       keeps search engines out
  vendor/          D3 and the Inter font (OFL licence), served locally
tests/             answer key, API security tests, dependency-injection tests
SECURITY.md        every security control, where it lives and which test covers it
requirements*.txt  pinned runtime, test (-dev) and Gemini dependencies
```

**Stack:** Python, FastAPI, SQLite, D3.js, vanilla JavaScript, Inter. No build step and no external requests at runtime. Optional: Gemini on Google Cloud Vertex AI.

## Security

Built around the four areas Aikido's AI Code Audit checks: business logic, IDOR, authentication and authorization. The full list of controls, each with a test, is in **[SECURITY.md](SECURITY.md)**. In short:

- **Authentication:** PBKDF2 passwords (600k iterations), server-side revocable sessions in an HttpOnly, Secure, SameSite=Strict cookie, working logout, login throttling, no user enumeration, and an audit log of every sign-in.
- **Authorization and IDOR:** one `AccessPolicy` for every rule; out-of-scope ids look exactly like missing ones; responses never leak ids from other scopes; CSRF tokens on every write.
- **Business logic:** atomic resolution (no double resolve, no race), no assignment stealing, only valid actions per issue type, written reasons for exceptions.
- **Hardening:** constant SQL with bound parameters only, strict input patterns, rate limiting, strict CSP and security headers, `robots.txt` plus `noindex`, pinned dependencies, no secrets in the repo.

## Data

All data in `/data` is **synthetic**: fictional clients, people and simplified policies written for this demo. It is not real SD Worx, client or legal information.

## What's unfinished

- Real sources (SharePoint, Teams, email) are simulated with JSON files; connectors are the next step.
- The Gemini engine is implemented and falls back safely, but the demo uses the rules engine and Gemini has not been evaluated on real documents.
- Gap detection groups questions with simple text similarity; embeddings would group paraphrases better.
- No notifications yet. Next step: push new high-pressure issues to the owner in Teams.
- Rate limiting and login throttling are kept in memory, so they reset on restart and are per server. A shared store such as Redis would be needed for several servers.
- Light theme only, no dark mode.
