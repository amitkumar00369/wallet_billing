# Wallet-Based Per-Minute Session Billing

Backend for paid 1:1 consultations. A user keeps a prepaid wallet, books a session with a provider, and is charged for every complete minute of the call. If the wallet cannot pay for the next minute, the system ends the session without letting the balance go below zero.

## Features

- Email + password accounts with two roles, `USER` and `PROVIDER`, and JWT login
- One wallet per user; balance can only change through recharges and billing
- Mock recharge: initiate, then a callback from a simulated payment provider
- Session lifecycle: `unassigned -> assigned -> accepted_by_provider -> in_progress -> completed`
- Per-minute billing with a ledger (`BillingTransaction`), safe to trigger repeatedly
- Automatic end of the session when the wallet cannot cover the next minute
- Idempotency for recharge callbacks and for billing, backed by database constraints

## Stack

Python 3.10+, Django 5.x, Django REST Framework, SimpleJWT, SQLite. No Celery, Redis or other infrastructure.

## Project structure

```
config/         settings, root urls, wsgi/asgi, factories.py (test helpers)
accounts/       custom User (email login, role), role permissions, JWT middleware, create_user command
wallets/        Wallet, RechargeTransaction, recharge + callback services and views
consultations/  ConsultationSession, state machine, transition services, session views
billing/        BillingTransaction, bill_session() and charge_minute()
docs/           state-machine.md, er-diagram.md
postman/        wallet_billing.postman_collection.json
```

Views are thin. Business rules live in each app's `services.py`; `consultations/state_machine.py` holds the allowed transitions.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # optional; defaults work for local use
set -a; source .env; set +a          # optional; loads the variables into your shell

python manage.py migrate             # creates db.sqlite3
python manage.py runserver 8000      # http://127.0.0.1:8000
```

Environment variables (all optional, see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev value | Django secret key |
| `DJANGO_DEBUG` | `1` | debug mode |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | allowed hosts |
| `SESSION_RATE_PER_MINUTE` | `50.00` | price per minute, copied onto a session when it starts |
| `RECHARGE_CALLBACK_SECRET` | `dev-callback-secret` | value expected in the `X-Callback-Secret` header |

### Creating test users

There is no signup endpoint (not required by the assignment). Use the management command; a wallet is created automatically:

```bash
python manage.py create_user --email asha@example.com   --password pass12345 --role USER
python manage.py create_user --email dr.rao@example.com --password pass12345 --role PROVIDER
```

### Running the tests

```bash
python manage.py test                       # all 75 tests
python manage.py test billing -v 2          # one app
```

Tests never sleep. Service functions accept `now=`, and API tests patch `django.utils.timezone.now` with a fake clock.

Mandatory scenarios (names start with `test_mandatory_`):

| # | Scenario | Test |
|---|---|---|
| 1 | Balance below 5 minutes cannot start | `consultations/tests.py::MandatoryScenarioTests` |
| 2 | Happy path with 2 billed minutes | `consultations/tests.py::MandatoryScenarioTests` |
| 3 | Balance runs out, system ends session | `billing/tests.py::MandatoryBillingScenarios` |
| 4 | End before start returns 400, nothing changes | `consultations/tests.py::MandatoryScenarioTests` |
| 5 | Duplicate recharge callback credits once | `wallets/tests.py::RechargeCallbackTests` |
| 6 | Duplicate billing does not double charge | `billing/tests.py::MandatoryBillingScenarios` |

## Authentication

SimpleJWT. Log in with email and password:

```
POST /api/v1/auth/token/           {"email": "...", "password": "..."}   -> {"access": "...", "refresh": "..."}
POST /api/v1/auth/token/refresh/   {"refresh": "..."}                    -> {"access": "..."}
```

Send `Authorization: Bearer <access>` on every `/private/` endpoint (see the next section).

## URL convention: public and private

All routes live under `/api/v1/<area>/`. If the URI has a `private` segment it needs a valid JWT; otherwise it is public.

| Kind | Pattern | Example | Checked by |
|---|---|---|---|
| Public | `/api/v1/<area>/<endpoint>/` | `POST /api/v1/recharge/callback/`, `POST /api/v1/auth/token/` | nothing (the callback checks its own shared secret) |
| Private | `/api/v1/<area>/private/<endpoint>/` | `GET /api/v1/wallet/private/balance/`, `POST /api/v1/sessions/private/1/start/` | JWT |

The JWT check is done by `accounts/middleware.py` for every path with a `private` segment, before routing, so a private endpoint can never be added without protection. Missing token gives `401 {"detail": "Authentication credentials were not provided."}`; a bad, expired or refresh token gives `401 {"detail": "Token is invalid or expired."}`. Roles (USER / PROVIDER) and ownership are then checked in the views.

## API

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/wallet/private/balance/` | any logged-in user | own balance |
| POST | `/api/v1/recharge/private/initiate/` | any logged-in user | start a recharge `{"amount": "300.00"}` |
| POST | `/api/v1/recharge/callback/` | mock payment provider | `{"reference": "...", "status": "success" or "failed"}` + `X-Callback-Secret` |
| POST | `/api/v1/sessions/private/create/` | USER | create a session (no body needed) |
| GET | `/api/v1/sessions/private/{id}/details/` | session's user or provider | details |
| POST | `/api/v1/sessions/private/{id}/assign/` | session's USER | `{"provider_id": 2}` |
| POST | `/api/v1/sessions/private/{id}/accept/` | assigned PROVIDER | accept |
| POST | `/api/v1/sessions/private/{id}/start/` | session's USER | start |
| POST | `/api/v1/sessions/private/{id}/end/` | session's USER or PROVIDER | end |
| POST | `/api/v1/sessions/private/{id}/bill/` | session's USER or PROVIDER | charge complete minutes not yet charged |

Status codes: `400` business rule broken (wrong state, low balance, second active session) or invalid input; `401` not logged in; `403` wrong role or bad callback secret; `404` unknown session, or a session you are not part of; `409` callback that contradicts the final state of a recharge.

### Postman collection

Import `postman/wallet_billing.postman_collection.json`. Base URL is the variable `base_url` (default `http://127.0.0.1:8000`). Create the two users from the setup section first, then run the folders in order: 1 Auth, 2 Wallet, 3 Recharge, 4a Session (up to start), wait 60+ seconds, 4b Billing and end, 5 JWT checks. Tokens, `provider_id`, the recharge reference and `session_id` are saved automatically by the test scripts, and every request has assertions. The whole collection was run with Newman against a live server (27 requests, 41 assertions, no failures).

### Example run

The responses below are real output from the running server (timestamps shortened).

```bash
# recharge
POST /api/v1/recharge/private/initiate/   {"amount": "300.00"}
201 {"reference": "rch_56d4adf8608a40dd9b76f0f4", "amount": "300.00", "status": "pending", "processed_at": null, ...}

# the provider calls back, twice
POST /api/v1/recharge/callback/   {"reference": "rch_56d4...", "status": "success"}
200 {"reference": "rch_56d4...", "status": "success", "credited": true}
200 {"reference": "rch_56d4...", "status": "success", "credited": false}

GET /api/v1/wallet/private/balance/
200 {"balance": "300.00", ...}

# session
POST /api/v1/sessions/private/create/
201 {"id": 1, "user": 1, "provider": null, "status": "unassigned", "per_minute_rate": null, ...}
POST /api/v1/sessions/private/1/assign/   {"provider_id": 2}
200 {"id": 1, "provider": 2, "status": "assigned", ...}
POST /api/v1/sessions/private/1/end/
400 {"detail": "Cannot end session 1: it is 'assigned' and cannot move to 'completed'."}
POST /api/v1/sessions/private/1/accept/        (as provider)
200 {"id": 1, "status": "accepted_by_provider", ...}
POST /api/v1/sessions/private/1/start/
200 {"id": 1, "status": "in_progress", "per_minute_rate": "50.00", "started_at": "2026-10-21T22:46:13Z", ...}

# 61 seconds later
POST /api/v1/sessions/private/1/bill/
200 {"session": {"status": "in_progress", "billed_minutes": 1, ...}, "minutes_charged": 1, "amount_charged": "50.00"}
POST /api/v1/sessions/private/1/bill/          (again)
200 {"session": {"billed_minutes": 1, ...}, "minutes_charged": 0, "amount_charged": "0.00"}
POST /api/v1/sessions/private/1/end/           (as provider)
200 {"status": "completed", "billed_minutes": 1, "duration_seconds": 61, "ended_by": "provider", "end_reason": "normal", ...}

GET /api/v1/wallet/private/balance/
200 {"balance": "250.00", ...}
```

When the wallet runs out, the session comes back as `"status": "completed", "ended_by": "system", "end_reason": "insufficient_balance"`.

## Recharge workflow

1. `POST /api/v1/recharge/private/initiate/` validates the amount (positive, at most 2 decimals) and creates a `pending` `RechargeTransaction` with a unique reference.
2. The payment provider calls `POST /api/v1/recharge/callback/` with the reference and an outcome. **This endpoint only simulates a payment provider; it is not a production payment integration.** It is protected by a shared secret header instead of a login.
3. `success`: status becomes `success` and the wallet is credited with the amount stored on the recharge. `failed`: status becomes `failed`, no credit.

Callback handling:

| Recharge is | Callback | Result |
|---|---|---|
| pending | success | credited once, 200, `credited: true` |
| pending | failed | marked failed, 200 |
| success | success (repeat) | nothing changes, 200, `credited: false` |
| failed | failed (repeat) | nothing changes, 200 |
| success | failed | 409, nothing changes |
| failed | success | 409, nothing changes |

Any `amount` in the callback body is ignored.

## Session lifecycle

See [docs/state-machine.md](docs/state-machine.md) and [docs/er-diagram.md](docs/er-diagram.md).

- The user creates a session and assigns a provider; the provider accepts; the user starts it.
- Starting requires `balance >= 5 x rate`, and no other `in_progress` session for that user.
- The rate is read from `SESSION_RATE_PER_MINUTE` once, at start, and stored on the session. Clients cannot send a rate, and later changes to the setting do not affect a running session.
- The user or the provider can end an `in_progress` session. Ending first charges any complete minutes still due.

## Billing policy

- A minute is charged only after it is **complete**. Billable minutes = `floor(elapsed_seconds / 60)`.
- 59 s = 0 minutes, 60 s = 1, 119 s = 1, 120 s = 2. Partial minutes are never charged.
- Billable minutes (`billed_minutes`) are kept separate from wall-clock time (`duration_seconds`). A call that lasts 2m59s is billed for 2 minutes and shows `duration_seconds = 179`.
- Billing is based on elapsed time, not on how often it is called. A late `bill` call catches up every missed minute in order.
- If minute N cannot be paid in full, nothing is deducted for it. The session is completed with `ended_by = system`, `end_reason = insufficient_balance`, `billed_minutes = N - 1`, `ended_at = started_at + (N - 1) minutes` and `duration_seconds = (N - 1) x 60`, even if billing runs much later.
- Billing can be triggered through the endpoint above or by calling `billing.services.bill_session(session_id, now=None)`. Only the session's own user or provider can use the endpoint.

## Idempotency and concurrency

**Recharge callback.** A conditional `UPDATE ... SET status = X WHERE reference = ? AND status = 'pending'` flips the status. Only the request whose UPDATE matched a row credits the wallet, in the same transaction. A repeat matches nothing.

**Billing.** Each minute is charged by `billing.services.charge_minute()` in one savepoint:
1. claim the minute with `UPDATE session SET billed_minutes = N WHERE id = ? AND status = 'in_progress' AND billed_minutes = N - 1`;
2. insert the `BillingTransaction` (unique on session + minute);
3. deduct with `UPDATE wallet SET balance = balance - rate WHERE user_id = ? AND balance >= rate`.

If any step fails, the whole savepoint rolls back, so there is never a deduction without a ledger row, or the reverse. If a retry or a second worker tries the same minute, step 1 matches no row (or step 2 raises `IntegrityError`, which is caught outside the savepoint) and the minute is skipped.

**What SQLite gives and does not give.** SQLite does not support row-level locks, and `select_for_update()` does nothing there, so this code does not use it. The protection comes from:
- conditional UPDATEs and unique/check constraints, which are enforced by the database engine itself;
- SQLite allowing one writer at a time. `settings.py` sets `transaction_mode: IMMEDIATE`, so every transaction takes the write lock when it begins and concurrent billing requests wait for each other instead of failing midway. This serialization is a property of SQLite, not something the code relies on for correctness: the conditional updates and constraints would still prevent double charges on a database with row-level locking, such as PostgreSQL.

## Known limitations

- Not tested under real parallel load. Tests cover repeated and stale calls (including a stale session object), but not multiple simultaneous processes.
- SQLite is a single-writer database; fine for this assignment, not for production throughput. Production would use PostgreSQL.
- Billing runs only when something calls it (the `bill` endpoint, or an end request). There is no scheduler. A cron job calling `bill_session` for every `in_progress` session would be the next step, and a session whose wallet is empty is only closed by the system when billing runs. When it runs late, the recorded end time is still backdated to the last paid minute.
- No cancel or reject path and no provider payouts; the assignment only requires the linear lifecycle.
- The callback is authenticated with a shared secret, not a signed payload. Fine for a mock.
- JWT tokens use SimpleJWT defaults (30 min access, 1 day refresh, no blacklist).
- A single fixed rate applies to all sessions (`SESSION_RATE_PER_MINUTE`); there are no per-provider rates.
- Accounts are created with a management command; there is no signup or password-reset API.
