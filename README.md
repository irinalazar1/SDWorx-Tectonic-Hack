# Fault Lines

**You can't fix what you can't see.** Fault Lines is a seismic map of an organisation's knowledge. It continuously scans policies, versions, owners, usage and questions, and makes the weak spots visible *before* someone gives a client the wrong answer.

Built for the **SD Worx challenge** at the Tectonic Hackathon (inspiration area: **Detect**: *how might conflicting, duplicated, missing or outdated knowledge become visible?*).

## What it detects

| On the map | Problem | How it is detected |
|---|---|---|
| 🔴 Fault line | **Conflict** | Two *active* items in the same country make different claims about the same subject |
| 🟡 Echo | **Duplicate** | Near-identical content maintained twice, often by different owners (a future conflict) |
| 🟤 Erosion | **Outdated** | Superseded version still in use, review date passed, or the owner left the company |
| ⚫ Crater | **Gap** | Questions people keep asking that no active item answers, including "covered in BE, not in NL" |

Every issue gets a **pressure score** (0–100) from real usage in the last 90 days: how often the knowledge was used, for how many clients, by how many colleagues. So owners fix the cracks people are actually falling into first.

## Why this is different from "another AI assistant"

Fault Lines never answers questions. It treats knowledge the way telecom operators treat service specifications: **every item is versioned, owned, scoped and moved through a lifecycle** (`draft → active → deprecated → retired`), and **every detected issue has a lifecycle too** (`detected → assigned → resolved / accepted`) with a full audit trail.

That makes "outdated" objective instead of a guess, and means fixes happen **at the source**: when an owner keeps one side of a conflict, the other side is deprecated, and every issue that depended on it clears automatically.

## Features

- **The map**: topics as terrain, items as nodes (BE white, NL blue, size = usage), issues drawn as fault lines, echoes, erosion rings and craters.
- **Time-lapse**: scrub or press play to watch knowledge drift: a duplicate appears, one copy gets updated, and a fault line opens.
- **Issue drawer**: both sides of a conflict side by side, the exact claims that disagree, owners, review dates, usage per month, affected clients, and how the problem formed.
- **Resolve at the source**: keep one version, retire, reconfirm and take ownership, assign a gap to yourself, or accept with a documented reason.
- **Trust badges in context**: open any document and see immediately if it is contradicted, outdated, orphaned or replaced.
- **Roles and scopes**: consultants see their own country read-only; knowledge owners resolve issues in their scope.

## Run it

Requirements: Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then set DEMO_PASSWORD (and SECRET_KEY)
python -m backend.app.seed                             # builds faultlines.db from /data and runs detection
uvicorn backend.app.main:app --port 8000
```

Open http://localhost:8000, pick an account and sign in with your `DEMO_PASSWORD`.

| Account | Role | Scope |
|---|---|---|
| Sofie Claes | Knowledge owner | BE + NL (best for the demo) |
| An Peeters | Knowledge owner | BE |
| Jeroen de Vries | Knowledge owner | NL |
| Tom Janssens | Consultant (read-only) | BE |
| Lotte Bakker | Consultant (read-only) | NL |

Re-run `python -m backend.app.seed` any time to reset the demo.

### Tests

```bash
pip install pytest
python -m pytest -q
```

- `tests/test_detection.py` is the **answer key**: every problem deliberately seeded into `/data` must be found, and nothing else.
- `tests/test_security.py` checks authentication, scope isolation (IDOR), role checks and input validation.

## How detection works

Two engines, same pipeline:

- **`rules`** (default, offline, deterministic): claims from the catalog, TF-IDF similarity for duplicates and unanswered questions, lifecycle rules for outdated knowledge. Runs anywhere with no API keys, so the demo never depends on Wi-Fi.
- **`gemini`** (optional): set `DETECTION_ENGINE=gemini` and `GOOGLE_CLOUD_PROJECT`, and `pip install -r requirements-gemini.txt`. Gemini on Vertex AI then extracts claims from each document's text and judges whether two claims really contradict, with a plain-language explanation. Any Gemini failure falls back to the rules engine.

```
data/*.json ─► seed ─► SQLite catalog (items, claims, usage, questions)
                              │
                     detection.run()
      conflicts · duplicates · outdated · gaps ─► pressure score
                              │
                   issues (with lifecycle + audit log)
                              │
          FastAPI  ─►  map · time-lapse · drawer · trust badges
```

## Project structure

```
backend/app/
  main.py        API, auth dependencies, scope checks, security headers
  detection.py   the four detectors + pressure score + issue lifecycle
  actions.py     resolution actions (keep / retire / reconfirm / assign / accept)
  llm.py         optional Gemini (Vertex AI) claim extraction and conflict judging
  security.py    PBKDF2 password hashing, HMAC-signed tokens, login throttling
  seed.py        builds the database from /data
data/            synthetic payroll knowledge for Belgium and the Netherlands
frontend/        index.html, app.js (D3, vendored), styles.css
tests/           answer key + security tests
```

## Security

- Passwords hashed with PBKDF2-SHA256; sessions are HMAC-signed, expiring tokens.
- Every endpoint checks scope server-side. Out-of-scope ids return 404, so they cannot be probed.
- Only owners can resolve, only within their country, and only with items that belong to the issue.
- Parameterised SQL only, validated input (Pydantic), login throttling, strict Content-Security-Policy, no inline scripts, all user data rendered as text.
- No secrets in the repo: `.env` is git-ignored; demo passwords come from the environment.

## Data

All data in `/data` is **synthetic**: fictional clients, people and simplified policies written for this demo. It is not real SD Worx, client or legal information.

## What's unfinished

- Connectors to real sources (SharePoint, Teams, email) are simulated with JSON files.
- The Gemini engine is implemented but the demo ships with the offline rules engine; it has not been evaluated on real documents.
- Gap detection clusters questions with simple text similarity; embeddings would group paraphrases better.
- Dark theme only; no notifications to owners yet (next step: send new high-pressure issues to the owner's Teams).
