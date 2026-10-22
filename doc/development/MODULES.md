# Smart Pricing Monitor — Module-by-Module Build Plan

> This document is the **single source of truth for building the system**. The modules are ordered so that
> each one depends only on modules before it, and each one ends in a runnable, tested state.
> The design lives in [`../architecture/architecture.html`](../architecture/architecture.html) and
> [`../architecture/README.md`](../architecture/README.md). This file turns that design into build instructions.

---

## 0. How to use this document

### 0.1 For the developer (you)

1. Build modules **in order**: `M00 → M01 → … → M23`. Optional modules (`M24`, `M25`) can come at the end.
2. To start a module, say **"Build M07"** (or whichever is next).
3. When a module is done, its **Definition of Done** commands pass and the **Progress tracker** (§1) is updated.
4. If you want to change a decision, change it **in this file first** (and add a line to the Decision log, §6), then build.

### 0.2 Protocol for Claude when asked to build module `Mxx`

1. Read §2 (Global conventions), §3 (Shared contracts), the `Mxx` section, and the architecture README.
2. Check the Progress tracker: every module listed under *Depends on* must be `✅ Done`. If one isn't, stop and say so.
3. Implement **only** the scope of `Mxx`. Anything listed under *Out of scope* is left for its own module.
4. Use the names, signatures, table and column names, event types, Redis keys, constants and error codes **exactly as
   written here**. If something has to deviate, record it in the Decision log (§6) and in the module notes.
5. Write every test listed under *Tests*, plus any others needed for confidence.
6. Run the Definition-of-Done commands and report the real output, failures included.
7. Update §1 (status → `✅ Done`, date) and write `doc/development/notes/Mxx.md` covering what was built, how to run it,
   deviations, and known limitations.
8. Don't start the next module unless asked.

---

## 1. Progress tracker

| # | Module | Phase | Depends on | Status |
|---|---|---|---|---|
| M00 | Repository, tooling & local infrastructure | Foundation | — | ✅ Done 2026-10-22 |
| M01 | Backend skeleton (config, logging, app factory, errors) | Foundation | M00 | ✅ Done 2026-10-22 |
| M02 | Domain model & ports | Foundation | M01 | ✅ Done 2026-10-22 |
| M03 | Database schema & migrations | Data | M02 | ✅ Done 2026-10-22 |
| M04 | Catalog seeds, repositories & catalog API | Data | M03 | ✅ Done 2026-10-22 |
| M05 | Normalizer **(B)** | Write path | M04 | ✅ Done 2026-10-22 |
| M06 | Guardrails **(C)** | Write path | M05 | ✅ Done 2026-10-22 |
| M07 | Event backbone (envelope, outbox, bus adapters, consumer runtime) | Write path | M03 | ⬜ |
| M08 | Device auth & `POST /v1/reports` | Write path | M06, M07 | ⬜ |
| M09 | Robust statistics library | Intelligence | M02 | ⬜ |
| M10 | Trust engine **(D)** | Intelligence | M09 | ⬜ |
| M11 | Anomaly classifier **(E)** | Intelligence | M10 | ⬜ |
| M12 | Fair-price band & hot cache **(F, G)** | Intelligence | M11 | ⬜ |
| M13 | Scoring worker (orchestration) | Intelligence | M08, M12 | ⬜ |
| M14 | Simulator & evaluation harness | Intelligence | M13 | ⬜ |
| M15 | Read API: `/check` **(A)**, `/compare`, `/trends` | Read & delivery | M13 | ⬜ |
| M16 | Subscriptions, alert dispatch & inbox | Read & delivery | M15 | ⬜ |
| M17 | Daily rollups, maintenance, digest & scheduler | Read & delivery | M16 | ⬜ |
| M18 | Metrics & observability | Read & delivery | M17 | ⬜ |
| M19 | PWA foundation + Price Check screen | Client | M15 | ⬜ |
| M20 | Report a Price + offline queue | Client | M19 | ⬜ |
| M21 | Compare & Trends + Alerts inbox (+ web push) | Client | M20, M16 | ⬜ |
| M22 | AWS infrastructure (CDK) | Cloud | M18, M21 | ⬜ |
| M23 | CI/CD, load testing, hardening & demo | Cloud | M22 | ⬜ |
| M24 | *(optional)* Receipt photos (S3 / MinIO) | Extension | M20 | ⬜ |
| M25 | *(optional)* ML upgrade behind the classifier interface | Extension | M14 | ⬜ |

Badges **(A)–(G)** refer to the differentiators in the architecture diagram.

---

## 2. Global conventions

### 2.1 Tech stack

| Area | Choice | Notes |
|---|---|---|
| Backend language | **Python 3.12** | Package manager: **uv** (lockfile committed) |
| API | **FastAPI** + **Pydantic v2** + **uvicorn** | Lambda via **Mangum** |
| DB | **PostgreSQL 16 + PostGIS 3.4** | **SQLAlchemy 2.0 (async)** + **asyncpg** + **GeoAlchemy2**, migrations with **Alembic** |
| Cache / local bus | **Redis 7** | `redis.asyncio` client |
| Config | **pydantic-settings** | env prefix `SPM_` |
| Logging | **structlog** (JSON) | |
| IDs | **UUIDv7** (`uuid-utils`) | time-ordered, which keeps indexes local |
| CLI | **typer** | single `spm` command |
| Tests | **pytest**, **pytest-asyncio**, **hypothesis**, **httpx** (ASGI transport) | |
| Quality | **ruff** (lint + format), **mypy** (strict on `spm.core`), **import-linter** | |
| Frontend | **React 18 + TypeScript + Vite**, **TanStack Query**, **React Router**, **Tailwind CSS**, **vite-plugin-pwa** (Workbox), **idb**, **Recharts** | Package manager: **pnpm**. Tests with **Vitest** + **Testing Library** |
| Infra | **AWS CDK v2 (Python)** | |
| Local mail | **Mailpit** (SMTP capture) | |

Pin exact versions in the lockfiles when M00 is built, using the latest stable release of each at that time.

### 2.2 Repository layout (target state)

```
smart-pricing-monitoring/
├── backend/
│   ├── pyproject.toml              # deps + ruff/mypy/pytest/import-linter config
│   ├── alembic.ini
│   ├── migrations/                 # Alembic env.py + versions/
│   ├── seeds/                      # areas.yaml, products.yaml, shops.yaml
│   ├── src/spm/
│   │   ├── config.py               # Settings
│   │   ├── constants.py            # tunable algorithm constants (§2.7)
│   │   ├── logging.py
│   │   ├── container.py            # wiring: builds adapters from Settings
│   │   ├── core/                   # PURE domain logic — no I/O, no frameworks
│   │   │   ├── models.py  errors.py  enums.py  geo.py  units.py
│   │   │   ├── normalizer.py  guardrails.py  stats.py  trust.py
│   │   │   ├── classifier.py  band.py  verdict.py  digest.py  events.py
│   │   ├── ports/                  # typing.Protocol interfaces
│   │   ├── adapters/
│   │   │   ├── postgres/           # ORM tables + repositories + UnitOfWork
│   │   │   ├── redis/              # hot cache, rate limiter, streams bus, dedup
│   │   │   ├── memory/             # in-memory fakes for tests & simulator
│   │   │   ├── aws/                # EventBridge publisher, SQS helpers, SNS, EMF metrics
│   │   │   └── notify/             # console, smtp, webpush notifiers
│   │   ├── services/               # application services (orchestrate core + ports)
│   │   ├── api/                    # app factory, routers/, schemas/, deps.py, errors.py
│   │   ├── workers/                # consumers: scoring, alerts, metrics; scheduled jobs
│   │   └── entrypoints/            # cli.py, lambda_api.py, lambda_sqs.py, lambda_jobs.py
│   └── tests/
│       ├── unit/                   # pure, no Docker needed
│       ├── integration/            # needs Postgres + Redis (marker: integration)
│       ├── e2e/                    # API + worker end-to-end (marker: e2e)
│       └── fixtures/
├── frontend/                       # Vite React PWA
├── infra/                          # CDK app
├── tools/simulator/                # synthetic data + evaluation
├── doc/
│   ├── architecture/
│   └── development/  (this file, notes/)
├── docker-compose.yml
├── Makefile
└── .github/workflows/
```

### 2.3 Architectural rules (enforced)

- **`spm.core` is pure.** It may import only the standard library, `spm.core.*` and `spm.constants`. No SQLAlchemy,
  Redis, FastAPI, boto3 or httpx. This is enforced with an **import-linter** contract that runs in `make check`.
- **Time is injected.** Core functions take `now: datetime` as an argument, and services get a `Clock` port.
  Never call `datetime.now()` inside `core/` or `services/`.
- **Ports and adapters.** Services depend on `ports/` protocols only, and `container.py` chooses adapters from settings.
  Every port has an in-memory adapter, so unit and e2e tests run without Docker where possible.
- **Async everywhere** in the API, services and adapters. Core is synchronous because it is pure computation.
- **No business logic in routers.** Routers handle validation, call one service method, and map the result.

### 2.4 Data conventions

| Topic | Rule |
|---|---|
| Timestamps | Always UTC `timestamptz` and timezone-aware `datetime`. Area-local time (`areas.timezone`) is used only for day boundaries, quiet hours and the digest schedule. |
| Money | `Decimal` at the boundaries. Raw price `NUMERIC(12,2)`; normalized price `NUMERIC(14,4)`. Statistics run on `float`, and results are converted back to `Decimal` (rounded half-even to 4 dp) before storage. API JSON returns prices as numbers rounded to 2 dp. |
| Currency | One currency per deployment: `SPM_CURRENCY` (e.g. `BDT`). Not stored per row. |
| IDs | Catalog entities (products, areas, shops) use **text slugs** (`onion`, `demo-central`, `shop-a`). Everything else uses UUIDv7. |
| Units | Base units are `kg`, `l` and `dozen`. Every price compared anywhere is **price per base unit**. |
| Durations | Configured in **hours** (`*_H`) or days (`*_D`) as the constant's name says. |

### 2.5 API conventions

- Prefix `/v1`. JSON only. Request bodies are limited to **16 KB**.
- Auth: `Authorization: Bearer <device JWT>` (M08). Admin endpoints use `X-Admin-Key`.
- Errors use **RFC 9457 problem details** (`application/problem+json`):

```json
{
  "type": "https://spm.dev/errors/price-out-of-bounds",
  "title": "Price is outside the plausible range",
  "status": 422,
  "code": "PRICE_OUT_OF_BOUNDS",
  "detail": "80000.00 per kg is above the maximum of 500.00 for onion.",
  "hints": [ { "message": "Did you mean 500 g instead of 1 kg?", "...": "..." } ],
  "request_id": "01J..."
}
```

**Error code registry** (use these exact codes):

| Code | HTTP | Raised by |
|---|---|---|
| `VALIDATION_ERROR` | 422 | schema validation |
| `UNKNOWN_PRODUCT` | 422 | normalizer (includes `suggestions`) |
| `UNIT_MISMATCH` | 422 | normalizer |
| `UNKNOWN_UNIT` | 422 | normalizer |
| `QUANTITY_OUT_OF_RANGE` | 422 | normalizer |
| `OBSERVED_AT_OUT_OF_RANGE` | 422 | guardrails |
| `UNKNOWN_SHOP` | 422 | guardrails |
| `PRICE_OUT_OF_BOUNDS` | 422 | guardrails (includes `hints`) |
| `TOO_FAR_FROM_SHOP` | 422 | guardrails (includes `distance_m`, `allowed_m`) |
| `RATE_LIMITED` | 429 | rate limiter (sets `Retry-After`) |
| `UNAUTHORIZED` | 401 | auth |
| `FORBIDDEN` | 403 | auth (banned reporter, bad admin key) |
| `NOT_FOUND` | 404 | any lookup |
| `INTERNAL` | 500 | unhandled errors (no stack trace in the body) |
| `BAD_REQUEST` | 400 | framework (unparseable body); fallback for other 4xx |
| `METHOD_NOT_ALLOWED` | 405 | routing (sets `Allow`) |
| `PAYLOAD_TOO_LARGE` | 413 | body size limit (16 KB) |
| `SERVICE_UNAVAILABLE` | 503 | `/readyz` (includes `checks`) |

### 2.6 Configuration (`spm/config.py`, env prefix `SPM_`)

| Variable | Default (local) | Purpose |
|---|---|---|
| `SPM_ENV` | `local` | `local` \| `test` \| `aws` |
| `SPM_DATABASE_URL` | `postgresql+asyncpg://spm:spm@localhost:5433/spm` | |
| `SPM_DB_POOL_SIZE` | `5` (use `1` on Lambda) | |
| `SPM_REDIS_URL` | `redis://localhost:6380/0` | |
| `SPM_EVENT_BUS` | `redis` | `memory` \| `redis` \| `eventbridge` |
| `SPM_NOTIFIER` | `console` | `console` \| `smtp` \| `sns` (inbox is always on) |
| `SPM_METRICS` | `log` | `log` \| `emf` |
| `SPM_JWT_SECRET` | *(required; random in local `.env`)* | HS256 device tokens |
| `SPM_JWT_TTL_D` | `90` | |
| `SPM_ADMIN_API_KEY` | *(required)* | admin endpoints |
| `SPM_IP_HASH_SALT` | *(required)* | salted SHA-256 of client IP |
| `SPM_CURRENCY` | `BDT` | display only |
| `SPM_DEFAULT_AREA` | `demo-central` | |
| `SPM_CORS_ORIGINS` | `http://localhost:5173` | comma-separated |
| `SPM_SMTP_HOST` / `_PORT` / `_FROM` | `localhost` / `1025` / `alerts@spm.local` | Mailpit |
| `SPM_VAPID_PUBLIC_KEY` / `_PRIVATE_KEY` / `_SUBJECT` | empty | web push (M21) |
| `SPM_AWS_EVENT_BUS_NAME` | `spm-bus` | |
| `SPM_SNS_TOPIC_ARN` | empty | |

### 2.7 Algorithm constants (`spm/constants.py`)

Every threshold lives here as a module-level `Final`, so tests and the simulator can override them in one place.

| Constant | Value | Used in |
|---|---|---|
| `GEOFENCE_BASE_M` | `300` | M06 |
| `GEOFENCE_ACCURACY_ALLOWANCE_MAX_M` | `200` | M06 — allowed = base + min(accuracy, this) |
| `NO_LOCATION_WEIGHT` | `0.5` | M10 — weight factor when the location is not verified |
| `OBSERVED_AT_MAX_AGE_H` | `168` (7 d) | M06 |
| `OBSERVED_AT_FUTURE_SKEW_MIN` | `5` | M06 |
| `DEDUPE_WINDOW_H` | `6` | M06 |
| `DEDUPE_PRICE_TOL` | `0.01` | M06 — relative difference |
| `RATE_LIMIT_PER_HOUR` / `_PER_DAY` | `30` / `100` | M06 — per reporter |
| `AUTH_RATE_LIMIT_PER_HOUR_PER_IP` | `20` | M08 |
| `BASE_QUANTITY_MIN` / `_MAX` | `0.05` / `50` | M05 — in base units |
| `FUZZY_MATCH_MIN_SCORE` | `85` | M05 |
| `MAD_SCALE` | `1.4826` | M09 |
| `MAD_FLOOR_FRAC` | `0.02` | M09 — MAD_eff ≥ 2% of the center (grocery prices are often identical) |
| `HALF_LIFE_MIN_H` / `_MAX_H` | `24` / `504` (21 d) | M09 |
| `HALF_LIFE_VOLATILITY_K` | `0.10` | M09 — half_life_days = K / σ_daily |
| `HALF_LIFE_MIN_HISTORY_D` | `30` | M17 — before this, use the seed default |
| `HALF_LIFE_MAX_DAILY_CHANGE` | `0.5` | M17 — ±50% per nightly update |
| `TRUST_PRIOR_ALPHA` / `_BETA` | `1.0` / `1.0` | M10 |
| `HIGH_TRUST_MIN_SCORE` | `0.80` | M10 |
| `HIGH_TRUST_MIN_EVIDENCE` | `10` | M10 — α + β − 2 ≥ this |
| `AGREE_LOG_TOL` | `0.05` | M10 — \|ln(p1/p2)\| ≤ tol counts as agreement |
| `DISAGREE_LOG_TOL` | `0.10` | M10 — \|ln(p1/p2)\| > tol counts as contradiction |
| `CORROBORATION_MIN_H` | `6` | M10 — window W = max(6 h, 0.5 × half-life) |
| `INDEPENDENCE_MIN_GAP_MIN` | `10` | M10 — same IP hash within 10 min → not independent |
| `BOUNDS_LOW_MULT` / `_HIGH_MULT` | `0.2` / `5.0` | M11 |
| `BOUNDS_WINDOW_D` | `90` | M11 |
| `BOUNDS_MIN_N` | `5` | M11 |
| `Z_THRESHOLD` | `3.0` | M11 |
| `Z_RESOLVE_NORMAL` | `2.0` | M10/M11 — "agrees with market" |
| `SHOP_HISTORY_D` | `30` | M11 |
| `SHOP_HISTORY_MIN_N` | `4` | M11 |
| `MARKET_MIN_N` | `5` | M11 |
| `MARKET_WINDOW_MIN_D` | `3` | M11 — market window = max(3 d, 2 × half-life) |
| `MARKET_SHIFT_WINDOW_H` | `48` | M11 |
| `MARKET_SHIFT_MIN_SHOPS` | `3` | M11 — and ≥ 50% of the area's active shops |
| `BAND_WINDOW_HALF_LIVES` | `4` | M12 |
| `BAND_WINDOW_MAX_D` | `60` | M12 |
| `BAND_MAX_SHOP_SHARE` | `0.40` | M12 |
| `CONF_HIGH_WEIGHT` / `_MEDIUM` / `_LOW` | `3.0` / `1.5` / `0.5` | M12 |
| `CONF_HIGH_MIN_SHOPS` | `3` | M12 |
| `SHOP_PRICE_MIN_FRESHNESS` | `0.25` | M12 — the shop price is shown only if its age ≤ 2 half-lives |
| `NEARBY_RADIUS_M` | `2000` | M15 |
| `CHEAPER_MIN_DIFF` | `0.03` | M15 — must be ≥ 3% cheaper |
| `CHEAPER_MAX_RESULTS` | `3` | M15 |
| `ALERT_DEDUP_H` | `6` | M16 |
| `MARKET_SHIFT_ALERT_DEDUP_H` | `24` | M16 |
| `ANOMALY_AUTO_RESOLVE_D` | `7` | M17 |
| `LOCATION_RETENTION_D` | `30` | M17 — raw report coordinates are nulled after this |

### 2.8 Testing conventions

- `pytest -m "not integration and not e2e"` must run in **< 10 s** with no Docker.
- Integration tests use the docker-compose Postgres (database `spm_test`, recreated per session) and Redis DB `15`
  (flushed per test).
- Coverage target: **≥ 90%** line coverage on `spm.core`, **≥ 75%** overall.
- Use **hypothesis** for the statistics and normalizer invariants.
- Name tests `test_<behaviour>_<condition>`.

### 2.9 Definition of Done (applies to every backend module)

```
make check        # ruff + format check + mypy + import-linter + unit tests
make test-int     # integration tests (Docker up)
```

Both must be green, the module notes must be written, and the tracker must be updated.

---

## 3. Shared contracts (referenced by many modules)

### 3.1 Event envelope (`spm/core/events.py`)

```json
{
  "id": "0192f0c4-…",            // UUIDv7, unique per event: the idempotency key
  "type": "price.reported",
  "version": 1,
  "occurred_at": "2026-10-10T08:15:02.123Z",
  "source": "spm.api",            // spm.api | spm.worker.scoring | spm.scheduler | …
  "correlation_id": "01J…",       // request_id of the originating HTTP request
  "data": { }
}
```

| Type | Producer | Consumers | `data` |
|---|---|---|---|
| `price.reported` | API (M08) | scoring (M13) | `report_id, received_at, product_id, shop_id, area_id, reporter_id, norm_price, observed_at` |
| `anomaly.detected` | scoring (M13) | alerts (M16), metrics (M18) | `anomaly_id, kind, direction, product_id, area_id, shop_id\|null, observed_price, reference_price, magnitude_pct, report_ids[], detected_at` |
| `band.updated` | scoring (M13) | metrics (M18) | `product_id, area_id, p25, p50, p75, confidence, computed_at` |
| `price.checked` | API (M15) | metrics (M18) | `check_id, product_id, area_id, verdict, quoted_norm_price, cheapest_norm_price\|null, base_quantity, potential_saving\|null` |
| `daily.tick` | scheduler (M17) | jobs (M17) | `job: "rollup"\|"digest"\|"maintenance"\|"outbox_sweep"\|"deferred_alerts", area_id\|null, for_date` |

Consumer groups (local Redis Streams) and queues (AWS SQS) are `scoring`, `alerts`, `metrics` and `jobs`.

### 3.2 Redis keyspace

| Key | Type | Content | TTL |
|---|---|---|---|
| `spm:band:{area}:{product}` | HASH | `p25 p50 p75 mad total_weight n_eff n_shops confidence newest_obs_at mean_age_h half_life_h rebase_at computed_at` | `max(2 × half-life, 24 h)` |
| `spm:cheap:{area}:{product}` | ZSET | member `shop_id`, score = shop price per base unit (fresh shops only) | same as band |
| `spm:shopprice:{area}:{product}` | HASH | field `shop_id` → JSON `{price, verified, verified_at, last_observed_at, n_reports}` | same as band |
| `spm:geo:shops` | GEO | member `shop_id` | none (rebuilt by seed and on startup) |
| `spm:rl:{scope}:{id}:{bucket}` | STRING | counter | window length |
| `spm:alert:dedup:{kind}:{area}:{product}:{shop\|*}:{direction}` | STRING | `1` (SET NX) | 6 h / 24 h |
| `spm:alerts:deferred` | ZSET | member = alert job JSON, score = deliver-at epoch (quiet hours) | none |
| `spm:events` / `spm:events:dlq` | STREAM | event envelopes | MAXLEN ~ 100k |

### 3.3 Classification labels

| Label | Meaning | Included in area band? | Included in shop price? | Alert? |
|---|---|---|---|---|
| `NORMAL` | consistent with the shop's history or the market | ✅ | ✅ | — |
| `PENDING` | \|z\| > 3 but uncorroborated; waiting for confirmation | ❌ | ❌ | — |
| `SHOP_OUTLIER` | this shop deviates from the market; corroborated | ❌ | ✅ | ✅ |
| `MARKET_SHIFT` | the whole area moved | ✅ (after re-base) | ✅ | ✅ (one per area) |
| `BAD_REPORT` | implausible, or contradicted by others | ❌ | ❌ | — (logged only) |
| `EXPIRED` | PENDING never resolved within its window | ❌ | ❌ | — |

---

## 4. Module dependency graph

```mermaid
flowchart LR
  M00-->M01-->M02-->M03-->M04-->M05-->M06-->M08
  M03-->M07-->M08
  M02-->M09-->M10-->M11-->M12-->M13
  M08-->M13-->M14
  M13-->M15-->M16-->M17-->M18
  M15-->M19-->M20-->M21
  M16-->M21
  M18-->M22
  M21-->M22-->M23
  M20-.->M24
  M14-.->M25
```

---

## 5. Modules

Each module section follows the same template: **Goal · Depends on · Deliverables · Specification · Tests · Definition of done · Out of scope.**

---

### M00 — Repository, tooling & local infrastructure

**Goal.** An empty but production-shaped monorepo where `make setup && make up && make check` works on a fresh machine.

**Depends on.** —

**Deliverables.**
- `git init`, with `.gitignore` (Python, Node, macOS, `.env`, `cdk.out`), `.editorconfig` and `.gitattributes`.
- Root `README.md`: what the project is, a link to the architecture, prerequisites (Docker, uv, Node 20+, pnpm) and a quickstart.
- `docker-compose.yml` services:
  - `db`: `postgis/postgis:16-3.4`, port `5433:5432`, user/password/db `spm`, a volume, and an init script
    `infra/local/db-init.sql` that creates the `spm_test` database and enables `postgis` in both databases.
  - `redis`: `redis:7-alpine`, port `6380:6379`, `--appendonly no`.
  - `mailpit`: `axllent/mailpit`, ports `8025` (UI) and `1025` (SMTP).
  - Healthchecks on all three.
- `backend/pyproject.toml` with:
  - runtime dependencies from §2.1, and dev dependencies (pytest, pytest-asyncio, hypothesis, ruff, mypy, import-linter, coverage).
  - `[tool.ruff]` line length 100, rules `E,F,I,B,UP,SIM,RUF,ASYNC`.
  - `[tool.mypy]` `strict = true` for `spm.core.*` and `spm.constants`, `check_untyped_defs` elsewhere.
  - `[tool.pytest.ini_options]` markers `integration` and `e2e`, `asyncio_mode = "auto"`.
  - `[tool.importlinter]` contract: `spm.core` must not import `sqlalchemy, redis, fastapi, boto3, httpx, spm.adapters, spm.api, spm.services, spm.workers`.
- `backend/src/spm/__init__.py` (with `__version__`) and an empty `spm/constants.py` with the header docstring.
- `.env.example` containing every variable from §2.6.
- `Makefile` targets:

| Target | Does |
|---|---|
| `setup` | `uv sync` in backend, `pnpm install` in frontend (if present), installs pre-commit |
| `up` / `down` | `docker compose up -d --wait` / `down` |
| `migrate` / `seed` | `spm migrate` / `spm seed` (wired in later modules) |
| `api` / `worker` / `scheduler` / `web` | run the processes (wired later) |
| `dev` | `up`, `migrate`, `seed`, then run api + worker + scheduler + web together |
| `lint` | ruff check + ruff format --check |
| `typecheck` | mypy + lint-imports |
| `test` | unit tests |
| `test-int` | integration + e2e tests |
| `check` | lint + typecheck + test |
| `sim` | simulator (M14) |

- `.pre-commit-config.yaml`: ruff, ruff-format, end-of-file-fixer, trailing-whitespace, check-yaml.
- `.github/workflows/ci.yml` (initial): on push/PR, set up uv, run `make lint typecheck test`.

**Tests.** One placeholder `tests/unit/test_smoke.py` that imports `spm` and checks `__version__`.

**Definition of done.** On a clean clone, `make setup && make up && make check` is green; `docker compose ps` shows all
three services healthy; Mailpit UI loads at `http://localhost:8025`.

**Out of scope.** Application code.

---

### M01 — Backend skeleton

**Goal.** A runnable FastAPI app with config, structured logging, problem+json errors, request IDs, health checks,
dependency container, CLI and Lambda entrypoint, with no business features yet.

**Depends on.** M00

**Deliverables.**
- `spm/config.py` — `class Settings(BaseSettings)` with every field from §2.6, `model_config = SettingsConfigDict(env_prefix="SPM_", env_file=".env")`,
  and `get_settings()` cached with `functools.lru_cache`. Validation: in `aws` env, the secrets must be non-default.
- `spm/logging.py` — `configure_logging(settings)` sets structlog to JSON (pretty console renderer when `SPM_ENV=local`).
  Context vars `request_id` and `correlation_id` are bound into every log line.
- `spm/container.py` — `class Container` holding `settings, clock, ids, db_engine, session_factory, redis, bus, notifier, metrics`.
  `async def build_container(settings) -> Container` and `async def close()`. In M01 only `settings, clock, ids` are real,
  and the rest stay `None` until their modules exist.
- `spm/api/app.py` — `create_app(settings: Settings | None = None) -> FastAPI` with:
  - a lifespan that builds and closes the container (`app.state.container`).
  - middleware: **RequestID** (reads `X-Request-ID` or generates a UUIDv7, echoes it in the response header);
    **BodySizeLimit** (16 KB → 413 problem); **ServerTiming** (adds `Server-Timing: app;dur=<ms>`); CORS from settings.
  - exception handlers producing problem+json (§2.5) for `SpmError` (base class in `core/errors.py`, see M02),
    `RequestValidationError` → `VALIDATION_ERROR` with the field list, `StarletteHTTPException`, and catch-all `INTERNAL`.
  - routers: `GET /healthz` → `{"status":"ok","version":…}`; `GET /readyz` pings DB and Redis when configured
    (503 problem if a configured dependency is down).
- `spm/api/deps.py` — `get_container(request)`, `get_settings_dep`.
- `spm/entrypoints/cli.py` — typer app `spm` with commands `api` (uvicorn, `--reload` in local), `worker`, `scheduler`,
  `migrate`, `seed` (the last four print "not implemented yet" until their modules land). Registered as a console script in pyproject.
- `spm/entrypoints/lambda_api.py` — `handler = Mangum(create_app(), lifespan="auto")`.
- `core/clock.py` *(the protocol lives in ports in M02, but put `SystemClock` and `FixedClock` here now)* — `SystemClock.now()` returns aware UTC.

**Tests.**
- `/healthz` returns 200 with the version.
- An unknown route returns a 404 problem with `code=NOT_FOUND` and a `request_id`.
- A body over 16 KB returns 413.
- `X-Request-ID` is echoed; a generated one is a valid UUID.
- A route that raises a bare `Exception` returns `INTERNAL` with no stack trace in the body.
- `/readyz` returns 503 when Redis is configured but unreachable (use an invalid URL).
- `Settings` refuses default secrets when `SPM_ENV=aws`.

**Definition of done.** `make check`; `make api` serves `http://localhost:8000/healthz`; `/docs` loads.

**Out of scope.** Database models, auth.

---

### M02 — Domain model & ports

**Goal.** Define the vocabulary of the system (entities, value objects, enums, errors) and every port interface,
with in-memory fakes for the basic ones. Pure code only.

**Depends on.** M01

**Deliverables.**

`core/enums.py` (all `StrEnum`):
- `BaseUnit {KG="kg", L="l", DOZEN="dozen"}`, `Dimension {MASS, VOLUME, COUNT}`
- `Classification {NORMAL, PENDING, SHOP_OUTLIER, MARKET_SHIFT, BAD_REPORT, EXPIRED}`
- `ScoringState {QUEUED="queued", SCORED="scored", FAILED="failed"}`
- `AnomalyKind {SHOP_OUTLIER, MARKET_SHIFT, BAD_REPORT}`, `Direction {UP="up", DOWN="down"}`
- `Confidence {HIGH="high", MEDIUM="medium", LOW="low", STALE="stale"}`
- `Verdict {GOOD_DEAL, FAIR, HIGH, SUSPICIOUS, UNKNOWN}`
- `AlertKind {SHOP_OUTLIER_HIGH, SHOP_OUTLIER_LOW, MARKET_SHIFT, DIGEST}`, `Channel {INBOX="inbox", EMAIL="email", WEBPUSH="webpush"}`

`core/models.py` — `@dataclass(frozen=True, slots=True)` types (fields shown; all datetimes aware UTC):

| Type | Fields |
|---|---|
| `GeoPoint` | `lat: float, lng: float` (validate ranges in `__post_init__`) |
| `Product` | `id, name, base_unit: BaseUnit, category, half_life_h: int, abs_min: Decimal, abs_max: Decimal, active: bool, aliases: tuple[str, ...]` |
| `Area` | `id, name, timezone: str, center: GeoPoint, quiet_start: time, quiet_end: time` |
| `Shop` | `id, name, area_id, location: GeoPoint, address: str, active: bool` |
| `Reporter` | `id: UUID, device_id: str, alpha: float, beta: float, reports_total: int, banned: bool, created_at` with property `trust` |
| `RawPriceInput` | `product_id: str \| None, product_text: str \| None, price: Decimal, quantity: Decimal, unit: str` |
| `NormalizedPrice` | `product_id, base_unit, base_quantity: Decimal, norm_price: Decimal, raw: RawPriceInput` |
| `LocationFix` | `point: GeoPoint, accuracy_m: float \| None` |
| `Report` | `id, received_at, reporter_id, client_report_id, product_id, shop_id, area_id, normalized: NormalizedPrice, observed_at, location: LocationFix \| None, distance_to_shop_m: float \| None, location_verified: bool, ip_hash: str \| None` |
| `Observation` | the scoring view of a report: `report_id, reporter_id, shop_id, norm_price: float, observed_at, trust: float, location_verified: bool, classification: Classification \| None, ip_hash: str \| None` |
| `Assessment` | `report_id, classification, direction: Direction \| None, z_shop: float \| None, z_market: float \| None, reference_price: float \| None, insufficient_data: bool, reason: str` |
| `Anomaly` | `id, kind, direction, product_id, area_id, shop_id, observed_price, reference_price, magnitude_pct, report_ids: tuple[UUID, ...], detected_at` |
| `FairBand` | `product_id, area_id, p25, p50, p75, mad: float, total_weight, n_eff: float, n_shops: int, confidence: Confidence, newest_obs_at: datetime \| None, mean_age_h: float \| None, half_life_h: int, rebase_at: datetime \| None, computed_at` |
| `ShopPrice` | `product_id, shop_id, area_id, price: float, verified: bool, support_weight: float, n_reports: int, last_observed_at, verified_at: datetime \| None` |
| `UnitHint` | `message: str, suggested_quantity: Decimal, suggested_unit: str, resulting_norm_price: Decimal` |

`core/errors.py` — `class SpmError(Exception)` with `code: str`, `status: int`, `title: str`, `detail: str` and `extra: dict`.
Add one subclass per code in §2.5 (`UnknownProduct`, `UnitMismatch`, …), each with fixed `code` and `status`.

`core/geo.py` — `haversine_m(a: GeoPoint, b: GeoPoint) -> float` (Earth radius 6 371 008.8 m).

`ports/` — `typing.Protocol` classes. Method signatures (all `async` unless marked sync):

```python
class Clock(Protocol):            def now(self) -> datetime          # sync
class IdGenerator(Protocol):      def new(self) -> UUID              # sync, UUIDv7

class CatalogReader(Protocol):
    async def products(self) -> list[Product]
    async def product(self, product_id: str) -> Product | None
    async def alias_map(self) -> dict[str, str]          # normalized alias -> product_id
    async def areas(self) -> list[Area]
    async def shops(self, area_id: str | None = None) -> list[Shop]
    async def shop(self, shop_id: str) -> Shop | None

class ReporterRepo(Protocol):
    async def get(self, reporter_id: UUID) -> Reporter | None
    async def get_by_device(self, device_id: str) -> Reporter | None
    async def create(self, reporter: Reporter) -> None
    async def add_evidence(self, reporter_id: UUID, d_alpha: float, d_beta: float) -> None
    async def trust_many(self, ids: Iterable[UUID]) -> dict[UUID, float]

class ReportRepo(Protocol):
    async def add(self, report: Report) -> None          # also inserts idempotency row + queued assessment
    async def by_client_id(self, reporter_id: UUID, client_report_id: UUID) -> UUID | None
    async def find_recent_duplicate(self, reporter_id, product_id, shop_id, since: datetime) -> list[tuple[UUID, Decimal]]
    async def get(self, report_id: UUID) -> Report | None

class AssessmentRepo(Protocol):
    async def observations_for_shop(self, product_id, shop_id, since: datetime) -> list[Observation]
    async def observations_for_area(self, product_id, area_id, since: datetime, exclude_shop: str | None = None) -> list[Observation]
    async def pending_for_shop(self, product_id, shop_id, since: datetime) -> list[Observation]
    async def save(self, assessment: Assessment, scored_at: datetime) -> None
    async def mark_trust_settled(self, report_ids: Iterable[UUID]) -> None
    async def get_state(self, report_id: UUID) -> tuple[ScoringState, Classification | None]

class AnomalyRepo(Protocol):
    async def open_if_absent(self, anomaly: Anomaly, dedup_key: str) -> bool   # False if one is already open
    async def recent(self, product_id, area_id, since) -> list[Anomaly]

class BandRepo(Protocol):
    async def get_band(self, product_id, area_id) -> FairBand | None
    async def upsert_band(self, band: FairBand) -> None
    async def get_rebase(self, product_id, area_id) -> datetime | None
    async def set_rebase(self, product_id, area_id, at: datetime) -> None
    async def upsert_shop_price(self, sp: ShopPrice) -> None
    async def shop_prices(self, product_id, area_id) -> list[ShopPrice]

class StatsRepo(Protocol):
    async def upsert_daily(self, rows: list[DailyStat]) -> None
    async def series(self, product_id, area_id, start: date, end: date, shop_id: str | None = None) -> list[DailyStat]
    async def area_median(self, product_id, area_id, since: datetime) -> tuple[float | None, int]

class HotCache(Protocol):
    async def get_band(self, area_id, product_id) -> FairBand | None
    async def put_band(self, band: FairBand, ttl_s: int) -> None
    async def put_shop_prices(self, area_id, product_id, prices: list[ShopPrice], ttl_s: int) -> None
    async def cheapest(self, area_id, product_id, max_price: float, limit: int) -> list[ShopPrice]
    async def shop_price(self, area_id, product_id, shop_id) -> ShopPrice | None
    async def shops_within(self, point: GeoPoint, radius_m: float) -> list[tuple[str, float]]   # (shop_id, distance_m)
    async def load_shop_geo(self, shops: list[Shop]) -> None

class RateLimiter(Protocol):
    async def hit(self, scope: str, key: str, limit: int, window_s: int) -> tuple[bool, int]  # (allowed, retry_after_s)

class EventPublisher(Protocol):
    async def publish(self, events: list[Event]) -> None
class Notifier(Protocol):
    async def send(self, channel: Channel, target: dict, title: str, body: str, data: dict) -> None
class Metrics(Protocol):
    def count(self, name: str, value: float = 1, **dims: str) -> None     # sync, non-blocking
    def gauge(self, name: str, value: float, **dims: str) -> None
    def timing(self, name: str, ms: float, **dims: str) -> None

class UnitOfWork(Protocol):          # async context manager; repos share one transaction
    reporters: ReporterRepo; reports: ReportRepo; assessments: AssessmentRepo
    anomalies: AnomalyRepo; bands: BandRepo; stats: StatsRepo; outbox: OutboxRepo
    subscriptions: SubscriptionRepo; alerts: AlertRepo; checks: PriceCheckRepo
    async def commit(self) -> None
    async def rollback(self) -> None
    async def advisory_lock(self, key: str) -> None     # transaction-scoped
```

(`OutboxRepo`, `SubscriptionRepo`, `AlertRepo`, `PriceCheckRepo` and `DailyStat` are defined in the modules that first need them: M07, M16 and M15/M17. Declare them here as placeholders.)

`adapters/memory/` — `FixedClock` (settable, `advance(hours=…)`), `SequentialIds` (deterministic UUIDv7s from a counter),
`MemoryRateLimiter`, `MemoryMetrics` (records calls). The other memory repositories are added in the modules that need them.

**Tests.**
- `GeoPoint` rejects lat > 90 and lng > 180.
- `haversine_m`: a known pair (e.g. two points 1 km apart along a meridian = 1 000 m ± 1 m), a zero distance, and symmetry.
- `Reporter.trust` equals α/(α+β).
- Every `SpmError` subclass has a unique `code` that appears in the §2.5 registry (parametrized test).
- `FixedClock.advance` works, and `SequentialIds` is monotonic.

**Definition of done.** `make check` (mypy strict passes on `spm.core`).

**Out of scope.** Persistence.

---

### M03 — Database schema & migrations

**Goal.** The full PostgreSQL schema as Alembic migrations, plus SQLAlchemy table definitions, an async engine
and a UnitOfWork skeleton.

**Depends on.** M02

**Deliverables.**
- `backend/alembic.ini` and `migrations/env.py` (async, reading `SPM_DATABASE_URL`).
- `adapters/postgres/tables.py` — SQLAlchemy Core/ORM table definitions matching the DDL below.
- `adapters/postgres/db.py` — `make_engine(settings)` (pool size from settings, `pool_pre_ping=True`) and `make_session_factory`.
- `adapters/postgres/uow.py` — `PgUnitOfWork(session_factory)` implementing `UnitOfWork`. Repository attributes are wired
  as their repositories get built. `advisory_lock(key)` → `SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))`.
- `spm migrate` CLI command → `alembic upgrade head`.
- Migration `0001_initial` creating the following (exact names):

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TYPE base_unit       AS ENUM ('kg','l','dozen');
CREATE TYPE classification  AS ENUM ('NORMAL','PENDING','SHOP_OUTLIER','MARKET_SHIFT','BAD_REPORT','EXPIRED');
CREATE TYPE scoring_state   AS ENUM ('queued','scored','failed');
CREATE TYPE anomaly_kind    AS ENUM ('SHOP_OUTLIER','MARKET_SHIFT','BAD_REPORT');
CREATE TYPE direction       AS ENUM ('up','down');

CREATE TABLE areas (
  id text PRIMARY KEY, name text NOT NULL, timezone text NOT NULL,
  center geography(Point,4326) NOT NULL,
  quiet_start time NOT NULL DEFAULT '22:00', quiet_end time NOT NULL DEFAULT '07:00',
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE products (
  id text PRIMARY KEY, name text NOT NULL, base_unit base_unit NOT NULL, category text NOT NULL,
  default_half_life_h int NOT NULL, half_life_h int NOT NULL,
  abs_min numeric(12,2) NOT NULL, abs_max numeric(12,2) NOT NULL CHECK (abs_max > abs_min),
  active boolean NOT NULL DEFAULT true, sort_order int NOT NULL DEFAULT 0);

CREATE TABLE product_aliases (alias text PRIMARY KEY, product_id text NOT NULL REFERENCES products(id), lang text);

CREATE TABLE shops (
  id text PRIMARY KEY, name text NOT NULL, area_id text NOT NULL REFERENCES areas(id),
  location geography(Point,4326) NOT NULL, address text NOT NULL DEFAULT '',
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX shops_location_gix ON shops USING gist(location);

CREATE TABLE reporters (
  id uuid PRIMARY KEY, device_id text NOT NULL UNIQUE,
  alpha real NOT NULL DEFAULT 1, beta real NOT NULL DEFAULT 1,
  reports_total int NOT NULL DEFAULT 0, banned boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL, last_seen_at timestamptz);

-- Immutable raw reports, partitioned by month on received_at
CREATE TABLE reports (
  id uuid NOT NULL, received_at timestamptz NOT NULL,
  reporter_id uuid NOT NULL, client_report_id uuid NOT NULL,
  product_id text NOT NULL, shop_id text NOT NULL, area_id text NOT NULL,
  raw_product_text text, raw_price numeric(12,2) NOT NULL, raw_quantity numeric(12,3) NOT NULL, raw_unit text NOT NULL,
  norm_price numeric(14,4) NOT NULL, base_quantity numeric(12,4) NOT NULL,
  observed_at timestamptz NOT NULL,
  location geography(Point,4326), location_accuracy_m real, distance_to_shop_m real,
  location_verified boolean NOT NULL, ip_hash text, receipt_key text,
  PRIMARY KEY (id, received_at)
) PARTITION BY RANGE (received_at);
CREATE INDEX reports_dupe_idx ON reports (reporter_id, product_id, shop_id, observed_at DESC);
-- + a DEFAULT partition and monthly partitions from the current month through +12 months (reports_yYYYYmMM)

CREATE TABLE report_idempotency (
  reporter_id uuid NOT NULL, client_report_id uuid NOT NULL, report_id uuid NOT NULL,
  received_at timestamptz NOT NULL, PRIMARY KEY (reporter_id, client_report_id));

-- Mutable scoring view (denormalized so that every statistics query hits only this table)
CREATE TABLE report_assessments (
  report_id uuid PRIMARY KEY, received_at timestamptz NOT NULL,
  reporter_id uuid NOT NULL, product_id text NOT NULL, shop_id text NOT NULL, area_id text NOT NULL,
  norm_price numeric(14,4) NOT NULL, observed_at timestamptz NOT NULL,
  location_verified boolean NOT NULL, ip_hash text,
  scoring_state scoring_state NOT NULL DEFAULT 'queued',
  classification classification, direction direction,
  z_shop real, z_market real, reference_price numeric(14,4),
  insufficient_data boolean NOT NULL DEFAULT false, reason text,
  trust_settled boolean NOT NULL DEFAULT false, resolved_by uuid,
  scored_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ra_area_idx ON report_assessments (product_id, area_id, observed_at DESC)
  INCLUDE (norm_price, classification, shop_id, reporter_id, location_verified);
CREATE INDEX ra_shop_idx ON report_assessments (product_id, shop_id, observed_at DESC);
CREATE INDEX ra_pending_idx ON report_assessments (product_id, shop_id) WHERE classification = 'PENDING';

CREATE TABLE anomalies (
  id uuid PRIMARY KEY, kind anomaly_kind NOT NULL, direction direction,
  product_id text NOT NULL, area_id text NOT NULL, shop_id text,
  observed_price numeric(14,4), reference_price numeric(14,4), magnitude_pct real,
  report_ids uuid[] NOT NULL, status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  dedup_key text NOT NULL, detected_at timestamptz NOT NULL, resolved_at timestamptz);
CREATE UNIQUE INDEX anomalies_open_dedup ON anomalies (dedup_key) WHERE status = 'open';
CREATE INDEX anomalies_lookup ON anomalies (product_id, area_id, detected_at DESC);

CREATE TABLE band_state (
  product_id text NOT NULL, area_id text NOT NULL,
  p25 numeric(14,4), p50 numeric(14,4), p75 numeric(14,4), mad numeric(14,4),
  total_weight real NOT NULL DEFAULT 0, n_eff real NOT NULL DEFAULT 0, n_shops int NOT NULL DEFAULT 0,
  confidence text NOT NULL DEFAULT 'stale', newest_obs_at timestamptz, mean_age_h real,
  half_life_h int NOT NULL, rebase_at timestamptz, computed_at timestamptz NOT NULL,
  PRIMARY KEY (product_id, area_id));

CREATE TABLE shop_prices (
  product_id text NOT NULL, shop_id text NOT NULL, area_id text NOT NULL,
  price numeric(14,4) NOT NULL, verified boolean NOT NULL, support_weight real NOT NULL,
  n_reports int NOT NULL, last_observed_at timestamptz NOT NULL, verified_at timestamptz,
  updated_at timestamptz NOT NULL, PRIMARY KEY (product_id, shop_id));

CREATE TABLE price_stats_daily (
  id bigserial PRIMARY KEY, product_id text NOT NULL, area_id text NOT NULL, shop_id text,
  day date NOT NULL, p25 numeric(14,4), p50 numeric(14,4), p75 numeric(14,4), mad numeric(14,4),
  n_reports int NOT NULL, total_weight real NOT NULL, final boolean NOT NULL DEFAULT false);
CREATE UNIQUE INDEX psd_uniq ON price_stats_daily (product_id, area_id, COALESCE(shop_id,'*'), day);

CREATE TABLE price_checks (
  id uuid PRIMARY KEY, created_at timestamptz NOT NULL, reporter_id uuid,
  product_id text NOT NULL, area_id text NOT NULL, shop_id text,
  quoted_norm_price numeric(14,4) NOT NULL, base_quantity numeric(12,4) NOT NULL,
  verdict text NOT NULL, band_p50 numeric(14,4), cheapest_norm_price numeric(14,4), potential_saving numeric(14,4));
CREATE INDEX price_checks_time ON price_checks (created_at DESC);

CREATE TABLE subscriptions (
  id uuid PRIMARY KEY, reporter_id uuid NOT NULL REFERENCES reporters(id),
  area_id text NOT NULL REFERENCES areas(id), product_id text REFERENCES products(id),  -- NULL = all products
  kinds text[] NOT NULL, channels text[] NOT NULL, email text, webpush jsonb,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL);
CREATE INDEX subs_lookup ON subscriptions (area_id, product_id) WHERE active;

CREATE TABLE digests (
  id uuid PRIMARY KEY, area_id text NOT NULL, day date NOT NULL, content jsonb NOT NULL,
  created_at timestamptz NOT NULL, UNIQUE (area_id, day));

CREATE TABLE alerts (
  id uuid PRIMARY KEY, reporter_id uuid NOT NULL, subscription_id uuid,
  anomaly_id uuid, digest_id uuid, kind text NOT NULL, title text NOT NULL, body text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL, read_at timestamptz,
  delivered jsonb NOT NULL DEFAULT '{}');
CREATE INDEX alerts_inbox ON alerts (reporter_id, created_at DESC);

CREATE TABLE outbox (
  id bigserial PRIMARY KEY, event_id uuid NOT NULL UNIQUE, event_type text NOT NULL,
  payload jsonb NOT NULL, created_at timestamptz NOT NULL, published_at timestamptz, attempts int NOT NULL DEFAULT 0);
CREATE INDEX outbox_unpublished ON outbox (created_at) WHERE published_at IS NULL;

CREATE TABLE processed_events (
  consumer text NOT NULL, event_id uuid NOT NULL, processed_at timestamptz NOT NULL,
  PRIMARY KEY (consumer, event_id));
```

- A SQL function `spm_ensure_report_partitions(months_ahead int)` that creates any missing monthly partitions.
  It is called by the migration and by the nightly maintenance job (M17).
- Test fixtures (`tests/integration/conftest.py`): a session-scoped migrated `spm_test` database, a per-test
  transaction rollback (or truncate) fixture, and a Redis DB 15 fixture.

**Tests (integration).**
- `alembic upgrade head` then `downgrade base` then `upgrade head` all succeed.
- Inserting into `reports` routes rows to the correct monthly partition.
- The `anomalies_open_dedup` partial unique index rejects a second open anomaly with the same key, but allows one once the first is resolved.
- `advisory_lock` blocks a second transaction with the same key (two concurrent sessions; the second waits until the first commits).
- `spm_ensure_report_partitions(3)` is idempotent.

**Definition of done.** `make check && make test-int`; `make migrate` works against the docker-compose DB.

**Out of scope.** Repositories other than the UnitOfWork shell; seeds.

---

### M04 — Catalog seeds, repositories & catalog API

**Goal.** Load the MVP catalog (1 area, 10 products, 5 shops, aliases), serve it from an in-process cache, put shop
coordinates into Redis GEO, and expose read-only catalog endpoints.

**Depends on.** M03

**Deliverables.**
- `seeds/areas.yaml`, `seeds/products.yaml`, `seeds/shops.yaml`. The values are **examples to replace with real data**.
  Prices are in `SPM_CURRENCY` units.

| id | name | base_unit | category | default half-life (h) | abs_min | abs_max | aliases (examples) |
|---|---|---|---|---|---|---|---|
| onion | Onion | kg | vegetable | 48 | 10 | 500 | piyaj, peyaj, piyaz, pyaz |
| potato | Potato | kg | vegetable | 96 | 5 | 300 | alu, aloo |
| tomato | Tomato | kg | vegetable | 48 | 10 | 600 | tometo |
| rice | Rice (coarse) | kg | staple | 336 | 20 | 400 | chal, chawal |
| lentils | Red lentils | kg | staple | 240 | 50 | 500 | dal, masoor, mosur dal |
| sugar | Sugar | kg | staple | 336 | 50 | 400 | chini |
| flour | Wheat flour | kg | staple | 336 | 20 | 300 | atta, ata |
| oil | Soybean oil | l | staple | 240 | 80 | 600 | tel, soybean oil |
| eggs | Eggs | dozen | protein | 120 | 60 | 400 | dim, anda, egg |
| milk | Milk | l | dairy | 168 | 40 | 300 | dudh, doodh |

  - Area: `demo-central` with a timezone (e.g. `Asia/Dhaka`), a center point and quiet hours 22:00–07:00.
  - Shops: `shop-a` … `shop-e` with **placeholder coordinates** spread 300–1 500 m from the area center.
- `spm seed` CLI: idempotent upsert of areas, products (it never overwrites `half_life_h` once it differs from the
  default, because M17 tunes it), aliases (normalized as in M05) and shops; then refreshes Redis GEO `spm:geo:shops`.
- `adapters/postgres/catalog_repo.py` — `PgCatalogReader` implementing `CatalogReader`.
- `services/catalog_cache.py` — `CatalogCache` wraps a `CatalogReader`, loads everything into memory, and refreshes it
  when older than **300 s** (lazy, on access). It exposes sync getters (`product(id)`, `shop(id)`, `alias_map`,
  `shops_in_area`), because the normalizer and guardrails need fast synchronous lookups.
- `adapters/redis/hot_cache.py` — this module implements only `load_shop_geo` and `shops_within` (GEOADD / GEOSEARCH BYRADIUS ASC WITHDIST).
- `adapters/memory/catalog.py` — `MemoryCatalogReader` built from the same YAML files (for unit tests).
- API routes (no auth):
  - `GET /v1/products` → `[{id, name, base_unit, category, aliases[], unit_presets[]}]`. `unit_presets` per base unit:
    kg → `[{q:250,u:"g"},{q:500,u:"g"},{q:1,u:"kg"},{q:2,u:"kg"},{q:5,u:"kg"}]`;
    l → `[{q:500,u:"ml"},{q:1,u:"l"},{q:2,u:"l"},{q:5,u:"l"}]`;
    dozen → `[{q:1,u:"piece"},{q:1,u:"hali"},{q:1,u:"dozen"},{q:1,u:"tray"}]`.
  - `GET /v1/areas` → `[{id, name, timezone, center}]`.
  - `GET /v1/shops?area=&lat=&lng=&radius_m=` → shops, with `distance_m` and sorted by distance when lat/lng are given
    (via `HotCache.shops_within`), otherwise sorted by name.

**Tests.**
- Unit: YAML validation (unique ids, aliases that map to existing products, abs_min < abs_max, valid timezone).
- Unit: `CatalogCache` refreshes after 300 s (with `FixedClock`) and not before.
- Integration: `spm seed` run twice gives the same row counts; a modified `half_life_h` survives a re-seed.
- Integration: `GET /v1/shops?lat&lng&radius_m=1000` returns only shops within 1 km, sorted ascending.

**Definition of done.** `make migrate seed` then `curl localhost:8000/v1/products` lists the 10 products.

**Out of scope.** Writes of any kind except seeding.

---

### M05 — Normalizer (B)

**Goal.** Turn whatever a user types ("piyaz", 40, 500, "gm") into a canonical `NormalizedPrice` per base unit, or
raise a precise error with suggestions. Pure code.

**Depends on.** M04 (for the catalog shape; the code itself is pure)

**Deliverables.**
- `core/units.py`
  - `UNIT_SYNONYMS: dict[str, str]` mapping surface forms to canonical units:
    `g, gm, gms, gram, grams → g`; `kg, kgs, kilo, kilos, kilogram → kg`; `ml, mls, millilitre, milliliter → ml`;
    `l, ltr, litre, liter, liters, litres → l`; `pc, pcs, piece, pieces, egg, eggs → piece`; `hali → hali`;
    `dozen, dz, doz → dozen`; `tray, tray30 → tray`.
  - `UNITS: dict[str, tuple[Dimension, Decimal]]` giving each canonical unit's dimension and factor to the base unit:
    `g (MASS, 0.001)`, `kg (MASS, 1)`, `ml (VOLUME, 0.001)`, `l (VOLUME, 1)`, `piece (COUNT, 1/12)`, `hali (COUNT, 4/12)`,
    `dozen (COUNT, 1)`, `tray (COUNT, 30/12)`. Store the fractions with `Decimal` division at 10 digits of precision.
  - `BASE_DIMENSION = {KG: MASS, L: VOLUME, DOZEN: COUNT}`.
  - `parse_unit(text) -> str` (lowercase, strip, strip a trailing "."; raises `UnknownUnit`).
- `core/normalizer.py`
  - `normalize_text(s) -> str`: Unicode NFKC, casefold, strip, remove all characters except letters, digits and spaces,
    collapse whitespace. (Letters from any script are kept, so Bangla or Hindi aliases work.)
  - `resolve_product(product_id, product_text, catalog) -> Product`:
    1. If `product_id` is given, look it up (inactive or missing → `UnknownProduct`).
    2. Otherwise apply `normalize_text`, then try an exact alias match, then an exact product id or name match.
    3. Otherwise fuzzy-match against all aliases and names with a pure Levenshtein ratio (implemented in `core`; no
       external library). A score ≥ `FUZZY_MATCH_MIN_SCORE` with a single best candidate resolves.
    4. Otherwise raise `UnknownProduct(suggestions=[top 3 product ids with score ≥ 60])`.
  - `normalize(raw: RawPriceInput, catalog) -> NormalizedPrice`:
    1. Validate `price > 0` and at most 2 decimal places; `quantity > 0`.
    2. Resolve the product and parse the unit, checking the dimension against the product's base unit (else `UnitMismatch`).
    3. `base_quantity = quantity × factor`, which must lie in [`BASE_QUANTITY_MIN`, `BASE_QUANTITY_MAX`]
       (else `QuantityOutOfRange`).
    4. `norm_price = price / base_quantity`, quantized to `0.0001` with `ROUND_HALF_EVEN`.
- The catalog here is any object exposing `product(id)`, `products()` and `alias_map()` synchronously (`CatalogCache` satisfies this).

**Tests.**
- Table-driven cases:

| Input | Expected |
|---|---|
| onion, 40, 500, "gm" | 80.0000 per kg, base_quantity 0.5 |
| "Piyaz", 75, 1, "kg" | product onion |
| eggs, 12, 1, "piece" | 144.0000 per dozen |
| eggs, 50, 1, "hali" | 150.0000 per dozen |
| eggs, 330, 1, "tray" | 132.0000 per dozen |
| milk, 45, 500, "ml" | 90.0000 per l |
| rice, 3000, 50, "kg" | 60.0000 per kg (sack) |
| onion, 40, 1, "l" | `UnitMismatch` |
| onion, 40, 10, "g" | `QuantityOutOfRange` (0.01 kg) |
| "oniun" | resolves to onion (fuzzy) |
| "xyz" | `UnknownProduct` with suggestions |

- Hypothesis: for any valid price and quantity, `norm_price × base_quantity ≈ price` (within 0.01).
- Hypothesis: the result does not depend on unit spelling (all synonyms of a unit give an identical result).

**Definition of done.** `make check`; `spm.core.normalizer` coverage ≥ 95%.

**Out of scope.** Unit-error hints (M06); HTTP wiring (M08).

---

### M06 — Guardrails (C)

**Goal.** Synchronous checks applied to every incoming report: freshness of `observed_at`, shop validity, absolute
bounds with "did you mean" hints, geo-fence, duplicates and rate limits. Pure rules in `core`, I/O in a service.

**Depends on.** M05

**Deliverables.**
- `core/guardrails.py` (pure):
  - `check_observed_at(observed_at, now)` raises `ObservedAtOutOfRange` when older than `OBSERVED_AT_MAX_AGE_H`
    or more than `OBSERVED_AT_FUTURE_SKEW_MIN` in the future. (The admin backfill in M14 passes `allow_backfill=True` to skip the age check.)
  - `check_absolute_bounds(np: NormalizedPrice, product, reference: float | None) -> None` raises
    `PriceOutOfBounds(hints=unit_error_hints(...))` when `norm_price ∉ [abs_min, abs_max]`.
  - `unit_error_hints(raw, product, reference) -> list[UnitHint]`:
    - Candidate corrections are other units of the same dimension with the same quantity number, plus the same unit
      with the quantity scaled by {×2, ×0.5, ×12, ÷12, ×1000, ÷1000}.
    - Keep the candidates whose resulting norm price falls within the bounds. Rank them by
      `|ln(candidate / reference)|`, where reference = band p50 if known, else `sqrt(abs_min × abs_max)`.
    - Return the top 2 with a message such as `"Did you mean 500 g instead of 500 kg?"`.
  - `geofence(shop: Shop, fix: LocationFix | None) -> tuple[bool, float | None]`:
    - No fix → `(False, None)`: accepted, but not location-verified.
    - Otherwise `d = haversine_m`, `allowed = GEOFENCE_BASE_M + min(accuracy_m or 0, GEOFENCE_ACCURACY_ALLOWANCE_MAX_M)`.
      If `d > allowed`, raise `TooFarFromShop(distance_m=d, allowed_m=allowed)`; otherwise return `(True, d)`.
  - `is_duplicate(new_price, recent: list[Decimal]) -> bool`: true if any recent price satisfies
    `|new − old| / old ≤ DEDUPE_PRICE_TOL`.
- `adapters/redis/rate_limiter.py` — `RedisRateLimiter.hit(scope, key, limit, window_s)`: fixed-window counter
  `spm:rl:{scope}:{key}:{floor(now/window)}` using `INCR`, plus `EXPIRE window_s` on the first increment, in a pipeline.
  Returns `(count ≤ limit, seconds until the window ends)`.
- `services/guardrail_service.py` — `GuardrailService(catalog_cache, rate_limiter, uow_factory, clock, hot_cache)`:
  `async def admit(reporter_id, raw: RawPriceInput, shop_id, observed_at, fix, *, allow_backfill=False) -> Admission`,
  where `Admission = {normalized, shop, location_verified, distance_m, duplicate_of: UUID | None}`.
  The **order matters** (cheapest and most informative first):
  1. Rate limits: hour then day (`scope="report"`), raising `RateLimited(retry_after)`.
  2. `normalize` (M05).
  3. `check_observed_at`.
  4. Shop exists and is active (`UnknownShop`).
  5. Absolute bounds (reference = cached band p50 if present).
  6. Geo-fence.
  7. Duplicates within `DEDUPE_WINDOW_H` (DB query `find_recent_duplicate`).
- Implement `ReportRepo.find_recent_duplicate` in `adapters/postgres/report_repo.py` (the rest of ReportRepo comes in M08).

**Tests.**
- Unit: the bounds hints. For onion with `price=40, quantity=500, unit="kg"` (0.08/kg, below abs_min=10), the hints
  include "500 g". For eggs with `price=12, unit="dozen"`, the hints include "1 piece".
- Unit: geo-fence at 299 m with no accuracy → OK; at 450 m with accuracy 200 → OK (allowed 500); at 520 m with accuracy 500 → reject (allowed capped at 500).
- Unit: duplicate detection at the 1% boundary.
- Unit: observed_at 8 days old → reject; 6 days → OK; 4 min in the future → OK; 6 min → reject.
- Integration: the rate limiter returns allowed for the first 30 hits and denied on the 31st, with a sensible retry_after; the key expires.
- Unit (memory adapters): `admit` short-circuits in the documented order (a rate-limited request never hits the normalizer, checked via a spy).

**Definition of done.** `make check && make test-int`.

**Out of scope.** Persistence of the report (M08).

---

### M07 — Event backbone

**Goal.** Reliable, idempotent, asynchronous messaging with a **transactional outbox**, a bus abstraction with
memory, Redis Streams and EventBridge adapters, and a consumer runtime with retries and a DLQ.

**Depends on.** M03

**Deliverables.**
- `core/events.py` — `Event` (frozen dataclass mirroring §3.1), one pydantic payload model per event type with
  `version`, `to_json()` / `from_json()` (orjson), and a `make_event(type, data, source, correlation_id, clock, ids)` helper.
- `ports/events.py` — `EventPublisher` (from M02) plus:
  ```python
  Handler = Callable[[Event], Awaitable[None]]
  class EventConsumer(Protocol):
      async def run(self, group: str, types: set[str], handler: Handler, *, stop: asyncio.Event) -> None
  class OutboxRepo(Protocol):
      async def add(self, events: list[Event]) -> None
      async def mark_published(self, event_ids: list[UUID], at: datetime) -> None
      async def unpublished(self, older_than: datetime, limit: int) -> list[Event]
      async def bump_attempts(self, event_ids: list[UUID]) -> None
  ```
- `adapters/postgres/outbox_repo.py`, `adapters/postgres/processed_repo.py` (`seen(consumer, event_id) -> bool` and
  `mark(consumer, event_id, at)`, done with `INSERT … ON CONFLICT DO NOTHING RETURNING`).
- `services/outbox.py`:
  - `OutboxPublisher.flush(event_ids)` is called **after commit** by services: it publishes and then marks the events published.
    A publish failure is logged and swallowed, because the sweeper retries.
  - `OutboxSweeper.sweep()` republishes events unpublished for more than 30 s (batch 100), bumping attempts. It runs
    every minute (from the scheduler in M17; locally also on worker start).
- Bus adapters:
  - `adapters/memory/bus.py` — `MemoryBus`: an in-process asyncio queue per group, used for tests and the simulator.
    It has `drain()`, which processes until empty (deterministic tests).
  - `adapters/redis/streams_bus.py` — publish = `XADD spm:events MAXLEN ~ 100000 * type <t> body <json>`.
    The consumer:
    - `XGROUP CREATE spm:events <group> $ MKSTREAM` (ignoring BUSYGROUP), then loops on `XREADGROUP … COUNT 10 BLOCK 5000 >`.
    - Events whose type isn't in `types` are ACKed and skipped. For the rest, it calls the handler and ACKs on success.
    - Every 30 s it runs `XAUTOCLAIM` for entries idle > 60 s. If the delivery count is ≥ 5, it moves the entry to
      `spm:events:dlq` (with the error) and ACKs it.
  - `adapters/aws/eventbridge_bus.py` — publisher only: `PutEvents` in batches of ≤ 10 with `Source="spm"`,
    `DetailType=<type>`, `Detail=<envelope json>` and `EventBusName`. Failed entries raise, so the outbox retries.
    (On AWS, consumption is via SQS → Lambda; see `entrypoints/lambda_sqs.py` in M13.)
- `workers/runtime.py` — `async def run_consumers(container, specs: list[ConsumerSpec])` with graceful shutdown on SIGINT/SIGTERM.
  `ConsumerSpec(group, types, handler)`. Also `idempotent(consumer_name, handler)`, a decorator that skips events
  already present in `processed_events` (it checks first and marks inside the handler's UoW, so the side effects and the
  mark commit atomically. The handler receives the UoW: `handler(event, uow)`).
- `spm worker` CLI: starts `run_consumers` with the specs registered so far (none yet, apart from a `log` consumer
  in local env that prints every event, enabled by `--debug-log-events`).

**Tests.**
- Unit (MemoryBus): publish → consume; a handler exception → redelivery up to 5 times → DLQ list.
- Unit: the `idempotent` wrapper processes the same event id only once.
- Integration (Redis): two consumer groups each receive every event; a crash-before-ACK is reclaimed by XAUTOCLAIM
  and redelivered; the poison message ends up in `spm:events:dlq` after 5 attempts.
- Integration (Postgres): an outbox row is written in the same transaction as a business row (rollback removes both);
  the sweeper republishes unpublished rows and marks them.
- Unit: the EventBridge adapter batches 23 events into 3 PutEvents calls (stub the boto3 client).

**Definition of done.** `make check && make test-int`; `make worker` runs and logs events published from a Python shell.

**Out of scope.** Business consumers.

---

### M08 — Device auth & `POST /v1/reports`

**Goal.** Anonymous device identity, and the complete synchronous write path:
validate → guardrails → persist (report + idempotency + queued assessment + outbox) → publish → `202`.

**Depends on.** M06, M07

**Deliverables.**
- **Auth**
  - `POST /v1/auth/device` with body `{"device_id": "<uuid generated by the client>"}`.
    Rate limited per IP hash (`AUTH_RATE_LIMIT_PER_HOUR_PER_IP`). It creates the reporter if absent (same `device_id`
    → same reporter, which makes it idempotent) and returns `{token, reporter_id, expires_at}`.
    The JWT is HS256 with claims `sub` (reporter_id), `did` (device_id), `iat`, `exp` and `iss = "spm"`.
  - `api/deps.py: current_reporter()` validates the token (401 `UNAUTHORIZED`), loads the reporter (403 `FORBIDDEN` if
    banned) and updates `last_seen_at` at most once per hour.
  - `ip_hash = sha256(SPM_IP_HASH_SALT + client_ip)` (client IP from `X-Forwarded-For` first hop, else the peer).
- **Repositories**: `PgReporterRepo` (all of `ReporterRepo`) and `PgReportRepo` (all of `ReportRepo`).
  `add()` inserts into `reports`, `report_idempotency` and `report_assessments` (state `queued`, denormalized fields)
  and increments `reporters.reports_total`.
- **Service** `services/report_service.py` — `ReportService.submit(reporter, cmd: SubmitReport, *, request_id, allow_backfill=False) -> SubmitResult`:
  1. If `reports.by_client_id(reporter, client_report_id)` exists, return that report (`duplicate=True`, HTTP 200).
  2. `guardrails.admit(...)`. If it reports `duplicate_of`, return that report (`duplicate=True`, HTTP 200, nothing stored).
  3. Build the `Report` (UUIDv7 id, `received_at = clock.now()`).
  4. In **one transaction**: `reports.add`, `outbox.add([price.reported])`, commit.
  5. `OutboxPublisher.flush`.
  6. `metrics.count("reports_received")`. Rejections are counted by an exception hook as `reports_rejected{code}`.
- **Routes**
  - `POST /v1/reports` (auth). Request:
    ```json
    {
      "client_report_id": "uuid",               // generated on the device; makes offline retries safe
      "product": "onion",                       // either product …
      "product_text": null,                     // … or free text
      "shop_id": "shop-a",
      "price": 40.00, "quantity": 500, "unit": "g",
      "observed_at": "2026-10-10T08:15:00Z",
      "location": { "lat": 23.7461, "lng": 90.3742, "accuracy_m": 25 }   // optional
    }
    ```
    Response `202` (new) or `200` (replay or duplicate):
    ```json
    {
      "report_id": "uuid", "status": "queued", "duplicate": false,
      "normalized": { "product_id": "onion", "price_per_base_unit": 80.0, "base_unit": "kg", "base_quantity": 0.5 },
      "location_verified": true, "distance_to_shop_m": 42.0
    }
    ```
  - `GET /v1/reports/{id}` (auth, own reports only) → `{report_id, status: queued|scored|failed, classification|null, verified: bool}`.
    `verified` = classification ∈ {NORMAL, SHOP_OUTLIER, MARKET_SHIFT} and the shop price is verified.
  - `GET /v1/me` → `{reporter_id, trust, reports_total, created_at}`.
  - `POST /v1/admin/reports/backfill` (admin key) takes the same body plus `reporter_device_id` and calls `submit(..., allow_backfill=True)`.
    It is used by the simulator. It is disabled when `SPM_ENV=aws` unless `SPM_ALLOW_BACKFILL=true`.

**Tests.**
- Unit (memory adapters + `MemoryBus`):
  - happy path → 202, a `price.reported` event with the correct payload, and a queued assessment.
  - the same `client_report_id` twice → second call returns 200 with the same id and no second event.
  - a near-identical price within 6 h with a different client id → 200 duplicate, nothing stored.
  - each guardrail error maps to the right problem code and status.
- Integration: full HTTP request against Postgres + Redis; the event appears in `spm:events`; the outbox row is
  marked published. Simulate a bus failure: the row stays unpublished, and `OutboxSweeper.sweep()` publishes it.
- Auth: a missing or expired token → 401; banned → 403; the same `device_id` returns the same `reporter_id`.

**Definition of done.** `make check && make test-int`. Manual: get a token with curl, submit a report, and see the
event in `make worker --debug-log-events`. `/reports` p95 < 100 ms locally on 200 sequential requests (quick script
in `tools/bench_reports.py`, reading `Server-Timing`).

**Out of scope.** Scoring (M13).

---

### M09 — Robust statistics library

**Goal.** Exact, well-tested numeric primitives used by the trust engine, the classifier and the band. Pure code.

**Depends on.** M02

**Deliverables.** `core/stats.py`, with all functions pure and typed (`Sequence[float]` in):

| Function | Definition |
|---|---|
| `weighted_quantile(values, weights, q) -> float` | Sort by value. With cumulative weights `S_i` and total `S`, define each point's position `c_i = (S_i − w_i/2) / S`. For `q ≤ c_1` return `v_1`; for `q ≥ c_n` return `v_n`; otherwise interpolate linearly between the bracketing points. Zero-weight points are dropped. Raises `ValueError` on empty input or `S == 0`. |
| `weighted_median(values, weights)` | `weighted_quantile(…, 0.5)` |
| `median(values)` | unweighted (all weights 1) |
| `mad(values, center=None) -> float` | median of \|x − center\|, with center defaulting to the median |
| `weighted_mad(values, weights, center=None)` | weighted median of \|x − center\| with the same weights |
| `robust_z(x, center, mad_value) -> float` | `(x − center) / (MAD_SCALE × max(mad_value, MAD_FLOOR_FRAC × abs(center)))`; returns 0.0 if the denominator is 0 |
| `freshness_weight(age_h, half_life_h) -> float` | `0.5 ** (max(age_h, 0) / half_life_h)` |
| `effective_n(weights) -> float` | `(Σw)² / Σw²` (0 if empty) |
| `log_ratio(a, b) -> float` | `ln(a / b)` |
| `daily_log_returns(series: Sequence[float]) -> list[float]` | consecutive `ln(p_t / p_{t−1})`, skipping non-positive values |
| `half_life_from_volatility(daily_medians) -> int \| None` | `σ = MAD_SCALE × mad(daily_log_returns)`; if fewer than 10 returns or σ == 0 → None; otherwise `round(clamp(HALF_LIFE_VOLATILITY_K / σ × 24, HALF_LIFE_MIN_H, HALF_LIFE_MAX_H))` |
| `cap_group_share(weights, groups, max_share) -> list[float]` | If any group's share of the total weight exceeds `max_share`, scale that group's weights down so its share equals `max_share` (one pass, then renormalize nothing; the caller uses relative weights). Returns new weights. |

**Tests.**
- Known values: `weighted_quantile([1,2,3,4],[1,1,1,1],0.5) == 2.5`; equal weights reproduce the interpolated median;
  a single value returns that value for every q.
- Weight 0 is ignored; all-zero weights raise `ValueError`.
- Hypothesis invariants: the result is monotone in q; it lies within [min, max]; it is invariant to permuting inputs;
  scaling all weights by a positive constant doesn't change it; adding a constant to all values shifts the result by that constant.
- `robust_z` with MAD = 0 and center = 80 uses the floor (0.02 × 80 = 1.6) → `robust_z(85, 80, 0) = 5 / (1.4826 × 1.6)`.
- `freshness_weight(48, 48) == 0.5`, `freshness_weight(0, x) == 1`.
- `half_life_from_volatility`: a constant series → None; a synthetic series with σ≈0.05 → ≈48 h; σ≈0.007 → clamped near 343 h.
- `cap_group_share`: one shop with 80% of the weight is capped to 40%.

**Definition of done.** `make check`; `spm.core.stats` coverage 100%.

**Out of scope.** Database access.

---

### M10 — Trust engine (D)

**Goal.** Reporter reputation, report weights, independence and agreement rules, the VERIFIED status, and the
resolution of PENDING reports. Pure functions returning *decisions*; the persistence happens in M13.

**Depends on.** M09

**Deliverables.** `core/trust.py`:

```python
def trust_score(alpha: float, beta: float) -> float                      # α / (α + β)
def is_high_trust(alpha: float, beta: float) -> bool                     # score ≥ HIGH_TRUST_MIN_SCORE and (α+β−2) ≥ HIGH_TRUST_MIN_EVIDENCE
def report_weight(trust: float, age_h: float, half_life_h: int, location_verified: bool) -> float
    # trust × freshness_weight(age_h, half_life_h) × (1.0 if location_verified else NO_LOCATION_WEIGHT)
def corroboration_window_h(half_life_h: int) -> float                    # max(CORROBORATION_MIN_H, 0.5 × half_life_h)
def agree(p1: float, p2: float) -> bool                                  # |ln(p1/p2)| ≤ AGREE_LOG_TOL
def contradict(p1: float, p2: float) -> bool                             # |ln(p1/p2)| > DISAGREE_LOG_TOL
def independent(a: Observation, b: Observation) -> bool
    # a.reporter_id != b.reporter_id
    # AND NOT (a.ip_hash and a.ip_hash == b.ip_hash and |a.observed_at − b.observed_at| < INDEPENDENCE_MIN_GAP_MIN)
def is_corroborated(target: Observation, others: Sequence[Observation], window_h: float, high_trust: bool) -> tuple[bool, list[UUID]]
    # True if high_trust, or if any other observation within ±window_h is independent and agrees.
    # Returns the supporting report ids.

@dataclass(frozen=True)
class TrustDelta:     reporter_id: UUID; d_alpha: float; d_beta: float; report_id: UUID; reason: str
@dataclass(frozen=True)
class PendingResolution:
    report_id: UUID
    new_classification: Classification       # SHOP_OUTLIER or BAD_REPORT
    resolved_by: UUID
    deltas: tuple[TrustDelta, ...]

def resolve_pending(pending: Sequence[Observation], new: Observation, new_z_market: float | None,
                    window_h: float) -> list[PendingResolution]
    # For each pending p within window_h of `new` and independent of it:
    #   agree(p, new)                                           → p becomes SHOP_OUTLIER; +1 α to p's reporter
    #   contradict(p, new) and new_z_market is not None and |new_z_market| ≤ Z_RESOLVE_NORMAL
    #                                                           → p becomes BAD_REPORT; +1 β to p's reporter
    #   otherwise                                               → unchanged

def settle_normal(new: Observation, peers: Sequence[Observation], window_h: float) -> list[TrustDelta]
    # Ongoing reputation for ordinary reports (only peers that are not yet settled):
    #   If ≥ 1 independent peer within the window agrees with `new` → +1 α to new's reporter and to each such peer's reporter.
    #   If ≥ 2 independent peers agree with each other and contradict `new` → +1 β to new's reporter.
    # Each report contributes to its reporter's evidence at most once (the caller marks trust_settled).

def verified_shop_price(obs: Sequence[Observation], now, half_life_h, high_trust: Callable[[UUID], bool]) -> tuple[bool, float | None, list[UUID]]
    # Takes observations for one shop and product, excluding PENDING / BAD_REPORT / EXPIRED, with age ≤ 2 × half-life.
    # Cluster: start from the most recent observation and take every observation that agrees with it.
    # VERIFIED if the cluster has ≥ 2 independent observations, or contains one high-trust reporter.
    # Price = weighted_median(cluster prices, report_weight(...)).
```

**Tests.**
- `trust_score(1,1) == 0.5`; `is_high_trust` is false for (9,1) (evidence 8) and true for (11,2).
- `report_weight` with no location halves the weight; an age of one half-life halves it again.
- Independence: same reporter → false; different reporters with the same ip_hash 5 min apart → false, 15 min apart → true.
- `agree(80, 83.9)` is true (ln ≈ 0.048); `agree(80, 85)` is false; `contradict(80, 90)` is true.
- `resolve_pending`: a pending report at 160 plus a new independent report at 158 → SHOP_OUTLIER with +α. A pending
  report at 160 plus a new report at 81 with z_market 0.3 → BAD_REPORT with +β. The same reporter → no resolution.
  A report outside the window → no resolution.
- `verified_shop_price`: one report → not verified; two agreeing independent reports → verified at their weighted median;
  one high-trust report → verified.
- Hypothesis: trust stays in (0, 1); weights are never negative.

**Definition of done.** `make check`; coverage ≥ 95% on `core.trust`.

**Out of scope.** Database writes.

---

### M11 — Anomaly classifier (E)

**Goal.** The 3-way decision ("who is wrong: the report, the shop, or the market?") as a pure function over a
prepared context.

**Depends on.** M10

**Deliverables.** `core/classifier.py`:

```python
@dataclass(frozen=True)
class ClassificationContext:
    now: datetime
    product: Product
    report: Observation                          # the new report (trust filled in)
    reporter_high_trust: bool
    area_median_90d: float | None                # unweighted, included classifications only
    area_n_90d: int
    shop_history: Sequence[Observation]          # same shop & product, SHOP_HISTORY_D, included labels, excluding this report
    market: Sequence[Observation]                # other shops, same area & product, market window, included labels (no SHOP_OUTLIER)
    same_shop_recent: Sequence[Observation]      # same shop & product within the corroboration window (any label except BAD_REPORT/EXPIRED), for corroboration
    shift_candidates: Mapping[str, ShiftEvidence]  # per active shop in the area: recent 48 h median vs own 30 d baseline
    active_shops_in_area: int

@dataclass(frozen=True)
class ShiftEvidence: shop_id: str; recent_median: float | None; baseline_median: float | None; baseline_mad: float | None

def build_shift_evidence(...) -> Mapping[str, ShiftEvidence]   # helper used by M13 (pure; takes observations grouped by shop)
def classify(ctx: ClassificationContext) -> Assessment
```

**Algorithm (implement exactly, in this order):**

1. **Weights.** Every observation's weight = `report_weight(trust, age_h, product.half_life_h, location_verified)`.
2. **Relative bounds.** If `area_n_90d ≥ BOUNDS_MIN_N` and `p < BOUNDS_LOW_MULT × M90` or `p > BOUNDS_HIGH_MULT × M90`,
   return `BAD_REPORT` (reason `"outside relative bounds"`, reference_price = M90).
3. **z_shop.** If `len(shop_history) ≥ SHOP_HISTORY_MIN_N`: `center = weighted_median(history)`, `m = weighted_mad(history)`,
   `z_shop = robust_z(p, center, m)`; otherwise None.
4. **z_market.** Use the market window `max(MARKET_WINDOW_MIN_D days, 2 × half-life)`. If `len(market) ≥ MARKET_MIN_N`:
   `center = weighted_median(market)`, `m = weighted_mad(market)`, `z_market = robust_z(p, center, m)`; otherwise None.
5. **Market shift.** Count shops `s` (including this report's shop, with this report added to its recent set) where
   `recent_median` and `baseline_median` exist and `robust_z(recent, baseline, baseline_mad) > Z_THRESHOLD` in the same
   direction as `p` relative to its own baseline. If `count ≥ MARKET_SHIFT_MIN_SHOPS` **and**
   `count ≥ ceil(0.5 × active_shops_in_area)`, return `MARKET_SHIFT` with direction = the sign and reference_price =
   the median of the baselines.
6. **Shop outlier candidate.** If `z_market` is not None and `|z_market| > Z_THRESHOLD`:
   - `(ok, support) = is_corroborated(report, same_shop_recent, window, reporter_high_trust)`.
   - If `ok`, return `SHOP_OUTLIER` (direction from the sign, reference = market center).
   - Otherwise return `PENDING`.
7. **Single-report jump at the shop with no market view.** If `z_market` is None and `z_shop` is not None and
   `|z_shop| > Z_THRESHOLD`, return `PENDING` (it is resolved later by corroboration or contradiction).
8. Otherwise return `NORMAL`. Set `insufficient_data = (z_shop is None and z_market is None)`.

Fill `z_shop`, `z_market` and `reference_price` whenever they were computed, and set a human-readable `reason`
(it's shown in the admin and simulator reports).

**Tests (table-driven scenario fixtures in `tests/fixtures/scenarios.py`).** The baseline is 5 shops of onion with
prices ~80 ± 2 over 30 days, 2 reports per shop per day, and honest reporters with trust 0.6.

| Scenario | New report | Expected |
|---|---|---|
| normal | shop-a 81 | NORMAL |
| typo ×1000 | shop-a 0.08 | BAD_REPORT (relative bounds) |
| 5× slip | shop-a 450 (inside the absolute bounds, but > 5 × the 90-day median of 80) | BAD_REPORT (relative bounds) |
| single spike | shop-c 120, no other recent report at shop-c | PENDING |
| corroborated spike | shop-c 120, with an independent shop-c 118 two hours earlier (that one being PENDING) | SHOP_OUTLIER, up |
| high-trust spike | shop-c 120 from a high-trust reporter | SHOP_OUTLIER |
| deal | shop-b 60, corroborated | SHOP_OUTLIER, down |
| market shock | shops a, b, c each with recent medians ~110 in the last 48 h; new report shop-d 112 | MARKET_SHIFT, up |
| partial shock | only 2 of 5 shops moved | not MARKET_SHIFT (PENDING or SHOP_OUTLIER per steps 6–7) |
| cold start | no history at all | NORMAL, insufficient_data |
| identical prices | the whole market is exactly 80 (MAD 0), new 82 | NORMAL (thanks to the MAD floor: z = 2/(1.4826·1.6) ≈ 0.84) |

**Definition of done.** `make check`; coverage ≥ 95% on `core.classifier`.

**Out of scope.** Fetching the context (M13), anomaly persistence (M13).

---

### M12 — Fair-price band & hot cache (F, G)

**Goal.** Compute the freshness-weighted fair band per `product × area` and the per-shop prices, and implement the
Redis hot layer used by the read path.

**Depends on.** M11

**Deliverables.**
- `core/band.py`:
  ```python
  BAND_INCLUDED = {NORMAL, MARKET_SHIFT}
  SHOP_INCLUDED = {NORMAL, MARKET_SHIFT, SHOP_OUTLIER}

  def band_window_start(now, half_life_h, rebase_at) -> datetime
      # now − min(BAND_WINDOW_HALF_LIVES × half_life_h hours, BAND_WINDOW_MAX_D days); if rebase_at is later, use rebase_at

  def compute_band(product_id, area_id, obs: Sequence[Observation], now, half_life_h, rebase_at) -> FairBand
      # 1. Keep obs with classification in BAND_INCLUDED and observed_at ≥ window start.
      # 2. w_i = report_weight(...); then cap_group_share(w, shop_ids, BAND_MAX_SHOP_SHARE).
      # 3. p25/p50/p75 = weighted_quantile; mad = weighted_mad around p50.
      # 4. total_weight = Σw (uncapped), n_eff = effective_n(capped w), n_shops = distinct shops with weight > 0.
      # 5. confidence:
      #      HIGH   if total_weight ≥ CONF_HIGH_WEIGHT and n_shops ≥ CONF_HIGH_MIN_SHOPS
      #      MEDIUM if total_weight ≥ CONF_MEDIUM
      #      LOW    if total_weight ≥ CONF_LOW
      #      STALE  otherwise (the quantiles may still be filled if any obs exist; None if none)
      # 6. newest_obs_at = max observed_at; mean_age_h = weight-averaged age.

  def compute_shop_prices(product_id, area_id, obs_by_shop: Mapping[str, Sequence[Observation]], now, half_life_h,
                          high_trust: Callable[[UUID], bool]) -> list[ShopPrice]
      # Per shop: filter SHOP_INCLUDED; drop it if the newest obs has freshness_weight < SHOP_PRICE_MIN_FRESHNESS;
      # (verified, price, support) = trust.verified_shop_price(...);
      # if not verified, price = the most recent included price (shown as "unverified").

  def cache_ttl_s(half_life_h) -> int     # max(2 × half_life_h, 24) × 3600
  ```
- `adapters/redis/hot_cache.py` (completing M04's partial adapter):
  - `put_band` → `HSET spm:band:{a}:{p}` with all fields (ISO timestamps; empty string for None) and `EXPIRE ttl`.
  - `get_band` → `HGETALL`, parsed to `FairBand` (None if missing).
  - `put_shop_prices` → in **one MULTI pipeline**: `DEL` both keys; `ZADD spm:cheap:{a}:{p}` for every shop
    (verified or not; the read path filters); `HSET spm:shopprice:{a}:{p}` with field = shop_id and value = JSON; `EXPIRE` both.
  - `cheapest(area, product, max_price, limit)` → `ZRANGEBYSCORE key -inf (max_price LIMIT 0 limit*3`, then hydrate from
    the hash, keep only the verified ones, and truncate to `limit`.
  - `shop_price(area, product, shop)` → `HGET`.
- `adapters/memory/hot_cache.py` — the equivalent memory version.
- `adapters/postgres/band_repo.py` — `PgBandRepo` (all of `BandRepo`).
- `services/band_service.py` — `BandService.recompute(uow, product, area_id, now) -> tuple[FairBand, list[ShopPrice]]`.
  It loads the area observations within the window (one query via `ra_area_idx`; a single query also gets the shop
  observations, since the window covers them), computes both, and upserts `band_state` and `shop_prices`.
  It also provides `publish_to_cache(band, shop_prices)` (called *after* commit) and
  `get_or_rebuild(area_id, product_id)` for the read path: on a Redis miss, read `band_state` and `shop_prices` from
  Postgres, repopulate Redis and return them.

**Tests.**
- Unit: with 4 shops at 80 and 1 shop at 120 (a SHOP_OUTLIER label), the band excludes 120, so p50 = 80.
- Unit: one shop with 20 reports and four shops with 1 each → the capped share keeps p50 near the four-shop consensus.
- Unit: freshness. Old reports (10 half-lives ago) at 60 and fresh ones at 80 → p50 ≈ 80.
- Unit: rebase. Reports before `rebase_at` are ignored.
- Unit: confidence levels at each threshold boundary; no observations → STALE with None quantiles.
- Unit: shop prices. A stale shop is dropped; an unverified one keeps its latest price and `verified=false`.
- Integration (Redis): put/get band round-trip; `cheapest` returns only verified shops below max_price, sorted; the TTL is set.
- Integration: `get_or_rebuild` repopulates Redis after a `FLUSHDB`.

**Definition of done.** `make check && make test-int`.

**Out of scope.** Triggering recomputation (M13); HTTP (M15).

---

### M13 — Scoring worker

**Goal.** Consume `price.reported`, run trust + classifier + band in one locked transaction, persist the results,
emit `anomaly.detected` and `band.updated`, and update Redis. This makes the full write → score loop work end to end.

**Depends on.** M08, M12

**Deliverables.**
- `adapters/postgres/assessment_repo.py` (all of `AssessmentRepo`) and `adapters/postgres/anomaly_repo.py` (all of `AnomalyRepo`).
  The observation queries join `reporters` to fill in `trust`.
- `services/scoring_service.py` — `ScoringService.handle(event: Event, uow: UnitOfWork) -> ScoringOutcome`:
  1. Parse the payload. Load the assessment state; if it is not `queued`, return (already scored, which makes the
     handler safe to replay).
  2. `await uow.advisory_lock(f"score:{product_id}:{area_id}")`. This serializes scoring per product and area, so band
     updates never race.
  3. Load `product` (catalog cache), the reporter's (α, β) → trust and high-trust status.
  4. Build `ClassificationContext` with these queries, all served by the `ra_*` indexes:
     - `area_median(product, area, now − 90 d)`.
     - shop history (30 d) and market (the market window, other shops).
     - same-shop recent (corroboration window).
     - per-shop 48 h and 30 d observations → `build_shift_evidence`.
  5. `assessment = classify(ctx)` → `assessments.save(...)` (state `scored`).
  6. **Pending resolution.** `resolve_pending(pending_for_shop(...), new_obs, assessment.z_market, window)`. For each
     resolution: update that assessment's classification and `resolved_by`, and apply the trust deltas. If one is
     promoted to SHOP_OUTLIER and the new report is also outlying in the same direction, set the new report to
     SHOP_OUTLIER too (it is now corroborated).
  7. **Reputation.** If the new report is NORMAL or MARKET_SHIFT, run `settle_normal(new, unsettled peers, window)`,
     then `reporters.add_evidence(...)` and `mark_trust_settled([...])`.
     For BAD_REPORT by relative bounds: `+0.5 β` (a light penalty; it may be an honest typo).
  8. **Anomalies.** For SHOP_OUTLIER (new or promoted) or MARKET_SHIFT:
     - Build the `Anomaly` with `magnitude_pct = (observed/reference − 1) × 100`.
     - `dedup_key = f"{kind}:{area}:{product}:{shop or '*'}:{direction}"` (shop is `*` for MARKET_SHIFT).
     - `open_if_absent` → if newly opened, add `anomaly.detected` to the outbox.
     - For MARKET_SHIFT, also `bands.set_rebase(product, area, now − MARKET_SHIFT_WINDOW_H)`.
  9. `band, shop_prices = BandService.recompute(...)` → add `band.updated` to the outbox.
  10. Upsert today's `price_stats_daily` rows (area-level and this shop), computed from the same observations
      (needs `StatsRepo.upsert_daily`; implement `PgStatsRepo` now, with `DailyStat` = product, area, shop|None, day, p25, p50, p75, mad, n_reports, total_weight, final).
  11. `processed_events.mark` → commit → `OutboxPublisher.flush` → `BandService.publish_to_cache`.
  12. Metrics: `count("reports_scored", classification=…)`, `timing("scoring_ms", …)`.
- `workers/scoring.py` — `ConsumerSpec(group="scoring", types={"price.reported"}, handler=idempotent("scoring", ScoringService.handle))`, registered in `spm worker`.
- Failure policy: transient errors (DB/Redis connection) are raised, so the bus retries. A `ValueError` from bad data
  sets the assessment to `failed` with the reason and returns normally (no infinite retries).
- `entrypoints/lambda_sqs.py` — a generic SQS handler. It parses the EventBridge envelope from each SQS record body
  (`detail`), dispatches by `detail-type` to the registered consumer, and returns `{"batchItemFailures": [...]}` for
  partial failures.

**Tests.**
- E2E (memory bus + Postgres): submit 10 normal reports from different reporters across 5 shops → all `scored/NORMAL`;
  `band_state` has p50 ≈ 80; Redis band present; a `band.updated` event is emitted for each report.
- E2E: a PENDING → SHOP_OUTLIER promotion opens exactly one anomaly and emits one `anomaly.detected`, even when a third
  agreeing report arrives (dedup).
- E2E: a market shock scenario → one MARKET_SHIFT anomaly, rebase set, and the band moves to the new level within the
  next report.
- E2E: a contradicted pending report becomes BAD_REPORT, and its reporter's β increases.
- Idempotency: delivering the same event twice gives no duplicate rows and no second set of events.
- Concurrency: 20 reports for the same product and area scored concurrently (asyncio.gather on the Redis bus) → final
  band = a fresh recompute from scratch (no lost updates).
- Lambda SQS handler: a malformed record goes in `batchItemFailures`, and the others succeed.

**Definition of done.** `make check && make test-int`. `make dev`; submit a few reports via curl; see the
classification via `GET /v1/reports/{id}` and the band in `redis-cli HGETALL spm:band:demo-central:onion`.
Scoring takes < 150 ms per event locally (log the timing).

**Out of scope.** Alerts delivery (M16).

---

### M14 — Simulator & evaluation harness

**Goal.** Measure the system against ground truth: generate realistic synthetic markets with known anomalies,
replay them through the real code path, and produce an evaluation report. This is the evidence for the project
write-up ("price differences detected", "freshness", "potential savings").

**Depends on.** M13

**Deliverables.** `tools/simulator/` (its own small package that uses `spm` as a library):
- `scenarios/*.yaml` — declarative scenarios:
  ```yaml
  name: onion_shock
  days: 45
  seed: 42
  area: demo-central
  products: [onion, rice, eggs]
  true_price: { onion: 80, rice: 60, eggs: 150 }       # per base unit at day 0
  daily_volatility: { onion: 0.04, rice: 0.005, eggs: 0.015 }  # geometric random walk σ
  shop_premium: { shop-a: 0.00, shop-b: -0.03, shop-c: 0.02, shop-d: 0.00, shop-e: 0.04 }
  events:
    - { day: 20, type: market_shock, product: onion, multiplier: 1.40, ramp_hours: 24 }
    - { day: 30, type: shop_gouging, product: eggs, shop: shop-c, multiplier: 1.30, duration_days: 5 }
  reporters:
    honest:    { count: 40, noise_sigma: 0.02, reports_per_day: 0.6 }
    sloppy:    { count: 6,  noise_sigma: 0.03, unit_error_rate: 0.2 }     # e.g. 500 g price entered as per kg
    malicious: { count: 2,  inflate: 0.6, target_shop: shop-a }
  location: { gps_rate: 0.8, accuracy_m: [10, 80] }
  checks: { per_day: 30, quote_noise: 0.15 }                             # simulated /check calls (used from M15 on)
  ```
- `generator.py` — produces a time-ordered stream of `SimReport` objects with **ground-truth labels**:
  `truth ∈ {normal, bad_unit, malicious, shop_outlier, market_shift}`.
- `runner.py` — two modes:
  - `--mode inprocess` (default): builds a container with `FixedClock`, `MemoryBus` and the real Postgres (test DB) and
    Redis. For each report it advances the clock, calls `ReportService.submit(..., allow_backfill=True)`, then
    `bus.drain()`. This is deterministic and fast (target: 45 days × ~50 reports/day in < 60 s).
  - `--mode http --base-url …`: posts to `/v1/admin/reports/backfill`, for running against a live stack.
- `evaluate.py` — joins the predictions (`report_assessments` + `anomalies`) with the truth and writes
  `tools/simulator/out/<scenario>-<timestamp>.md` and `.json` with:
  - a confusion matrix (truth × classification) and per-class precision and recall.
  - for each true event: detected? time-to-detect (hours from the start of the event to the first anomaly) and the number of alerts.
  - false alarms: anomalies with no matching true event.
  - band accuracy: MAPE of band p50 against the true area price, per product per day.
  - the freshness distribution (median report age per product).
  - guardrail rejections by code.
- `make sim SCENARIO=onion_shock` and `make sim-all`.
- Default scenarios: `baseline_quiet`, `onion_shock`, `egg_gouging`, `unit_typos`, `spam_attack` (one reporter, 200 reports/hour → rate limited), `cold_start`.

**Acceptance targets** (they are checked by `tests/e2e/test_sim_targets.py` with smaller versions of the scenarios):
- `unit_typos`: BAD_REPORT recall on `bad_unit` ≥ 0.90.
- `onion_shock`: MARKET_SHIFT detected within 24 h of the shock start, with ≤ 1 MARKET_SHIFT anomaly per product per direction.
- `egg_gouging`: SHOP_OUTLIER for shop-c detected within 12 h, with no SHOP_OUTLIER on other shops.
- `baseline_quiet`: ≤ 1 false SHOP_OUTLIER per product per 30 days.
- Band MAPE ≤ 5% on `baseline_quiet` after day 3.

If a target fails, **tune the constants in `spm/constants.py`** and record the change and the reason in the Decision log.
Don't special-case the code.

**Definition of done.** `make sim-all` produces reports and the target tests pass. Commit one sample report to `doc/evaluation/`.

**Out of scope.** UI for the reports (plain Markdown is fine).

---

### M15 — Read API: `/check` (A), `/compare`, `/trends`

**Goal.** The user-facing read endpoints, served from Redis in O(1) with a fallback to Postgres, plus the verdict logic.

**Depends on.** M13

**Deliverables.**
- `core/verdict.py`:
  ```python
  def verdict(norm_price: float, band: FairBand | None) -> tuple[Verdict, float | None, float | None]
      # returns (verdict, pct_vs_median, z)
      # band None or confidence STALE                → UNKNOWN
      # z = robust_z(p, band.p50, band.mad)
      # z > Z_THRESHOLD                              → SUSPICIOUS
      # p > band.p75                                 → HIGH
      # p < band.p25                                 → GOOD_DEAL   (z < −Z_THRESHOLD is still GOOD_DEAL, with message "unusually low; check quality")
      # otherwise                                    → FAIR
  def verdict_message(v, pct, currency) -> str        # short human text, e.g. "About 17% above the fair price in this area."
  def freshness_label(age_minutes) -> str             # "just now", "35 min ago", "2 h ago", "3 days ago"
  ```
- `services/check_service.py` — `CheckService.check(q: CheckQuery, reporter_id | None) -> CheckResult`:
  1. `normalize` (no guardrail bounds here; a nonsense quote just gets a SUSPICIOUS or GOOD_DEAL verdict).
  2. Resolve the area: explicit `area` > the shop's area > the nearest area center to lat/lng > `SPM_DEFAULT_AREA`.
  3. `band = hot_cache.get_band` or `band_service.get_or_rebuild`.
  4. `verdict`.
  5. Shop: `hot_cache.shop_price(area, product, shop_id)` if a shop is given.
  6. Cheaper options:
     - `cheapest(area, product, max_price = p × (1 − CHEAPER_MIN_DIFF), limit = CHEAPER_MAX_RESULTS)`.
     - If lat/lng are given, intersect with `shops_within(point, NEARBY_RADIUS_M)` and attach `distance_m`.
     - Exclude the quoted shop.
     - `saving_per_base_unit = p − price` and `saving_for_quantity = saving × base_quantity`.
  7. Emit `price.checked` **without** waiting on the DB: put it on the outbox-free direct publish path
     (`bus.publish` in a background task). A lost check event is acceptable, since it only feeds metrics. The metrics
     consumer (M18) persists `price_checks`.
  8. Target: no Postgres access on a cache hit.
- Routes (auth optional; if a token is present, the reporter_id is recorded):
  - `GET /v1/check?product=onion&price=95&quantity=1&unit=kg&shop_id=shop-c&lat=&lng=&area=`
    (`product` accepts an id or free text, both resolved by the normalizer). Response:
    ```json
    {
      "product": {"id": "onion", "name": "Onion", "base_unit": "kg"},
      "quoted": {"price": 95.0, "quantity": 1, "unit": "kg", "price_per_base_unit": 95.0},
      "verdict": "HIGH",
      "message": "About 17% above the fair price in this area.",
      "pct_vs_median": 17.3,
      "fair_band": {"low": 72.0, "median": 81.0, "high": 84.0},
      "confidence": "high",
      "freshness": {"newest_report_at": "…", "age_minutes": 120, "label": "2 h ago", "stale": false},
      "shop": {"id": "shop-c", "name": "…", "price_per_base_unit": 94.0, "verified": true, "age_minutes": 95},
      "cheaper_options": [
        {"shop_id": "shop-b", "name": "…", "price_per_base_unit": 76.0, "distance_m": 400,
         "saving_per_base_unit": 19.0, "saving_for_quantity": 19.0, "verified": true, "age_minutes": 95}
      ],
      "currency": "BDT",
      "computed_at": "…"
    }
    ```
  - `GET /v1/compare?product=onion&area=&lat=&lng=&radius_m=2000` → `{product, band (same shape as fair_band + confidence + freshness), shops: [{shop_id, name, distance_m|null, price_per_base_unit|null, verified, age_minutes|null, vs_median_pct|null, open_anomaly: {kind, direction, magnitude_pct}|null}]}`, sorted by price (nulls last).
    Shop prices come from the Redis hash; open anomalies come from `AnomalyRepo.recent` (one small query; acceptable
    here, since `/compare` has no 50 ms budget, but cache the result for 60 s in Redis under `spm:compare:{a}:{p}`).
  - `GET /v1/trends/{product}?area=&days=30` (days ∈ [7, 90]) → `{series: [{day, p25, p50, p75, n_reports}], anomalies: [{detected_at, kind, direction, magnitude_pct, shop_id}], current_band, change_7d_pct, change_30d_pct}`.
    Data comes from `price_stats_daily` (area-level rows). Days with no data are omitted (the client draws gaps).
    `Cache-Control: max-age=300`.

**Tests.**
- Unit `verdict`: each branch, including the STALE → UNKNOWN case and the MAD-floor case.
- Unit `freshness_label` boundaries.
- Unit `CheckService` with memory adapters: the cheaper options exclude the quoted shop, unverified shops and shops
  outside the radius; savings are computed correctly for a 500 g quote; a cache hit does **zero** repository calls
  (spy on the UoW factory).
- Integration: after the scoring E2E fixture, `/v1/check` returns HIGH for 95 and FAIR for 80; after `FLUSHDB` the
  answer is unchanged (rebuilt from Postgres).
- Integration: `/v1/trends/onion?days=30` returns ascending days; `/v1/compare` sorts by price with nulls last.
- Performance: `tools/bench_check.py` hits `/v1/check` 1 000 times; p95 of `Server-Timing app;dur` < 50 ms locally.

**Definition of done.** `make check && make test-int`; the bench output is pasted into the module notes.

**Out of scope.** Subscriptions and alerts (M16).

---

### M16 — Subscriptions, alert dispatch & inbox

**Goal.** Users subscribe to products in an area. Anomalies turn into deduplicated, quiet-hours-aware alerts,
delivered to an in-app inbox plus optional email (SMTP locally, SNS on AWS) and web push (M21).

**Depends on.** M15

**Deliverables.**
- Repositories: `SubscriptionRepo` (`create, list_for_reporter, deactivate, matching(area_id, product_id, kind) -> list[Subscription]`)
  and `AlertRepo` (`add, inbox(reporter_id, cursor, limit), mark_read(id, reporter_id), mark_delivered(id, channel, at)`).
- `core/alerts.py` (pure):
  - `alert_kind(anomaly) -> AlertKind` (SHOP_OUTLIER up → `SHOP_OUTLIER_HIGH`, down → `SHOP_OUTLIER_LOW`, MARKET_SHIFT → `MARKET_SHIFT`).
  - `render(anomaly, product, shop | None, area, currency) -> (title, body)`. Templates:
    - HIGH: "Onion is 38% above the area price at Shop C" / "Shop C: 110.00/kg vs. area median 80.00/kg (3 verified reports). Cheaper nearby: Shop B 76.00/kg."
    - LOW: "Deal: Eggs 20% below the area price at Shop B"
    - MARKET_SHIFT: "Onion prices rose ~40% across Demo Central" / "Most shops moved in the last 48 h. The fair price is now 108–115/kg."
  - `in_quiet_hours(now_utc, area) -> bool` and `next_delivery_time(now_utc, area) -> datetime` (handling windows that
    cross midnight, in the area's timezone).
- `services/alert_service.py` — `AlertService.on_anomaly(event, uow)` (consumer group `alerts`, idempotent):
  1. Load the anomaly, product, shop and area; compute the kind.
  2. **Dedup.** `SET spm:alert:dedup:{kind}:{area}:{product}:{shop|*}:{direction} 1 NX EX <ALERT_DEDUP_H or MARKET_SHIFT_ALERT_DEDUP_H>`.
     If the key exists, stop (count `alerts_suppressed{reason=dedup}`).
  3. For each matching active subscription (kind in `subscription.kinds`, product matches or is NULL):
     - Insert an `alerts` row (the inbox, always).
     - For the email and webpush channels: if in quiet hours, `ZADD spm:alerts:deferred <deliver_at> <job json>`;
       otherwise call `notifier.send`. Record the delivery in `alerts.delivered`.
  4. Notification failures are logged and recorded (`delivered.email = {"error": …}`), never raised, so a bad email
     address can't block other subscribers.
- `services/deferred_alerts.py` — `flush_due(now)` pops due jobs (`ZRANGEBYSCORE … LIMIT 100`, then `ZREM`) and sends
  them. It is called every minute by the scheduler (M17); until then, call it from the worker's loop every 60 s.
- Notifier adapters:
  - `ConsoleNotifier` (logs the message).
  - `SmtpNotifier` (`aiosmtplib`, plain-text + simple HTML; works with Mailpit).
  - `SnsNotifier` (`publish` to `SPM_SNS_TOPIC_ARN` with message attributes `area`, `product`, `kind`; email
    subscribers use SNS filter policies; see M22).
  - A `WebPushNotifier` stub that raises NotImplemented until M21.
  - `MultiNotifier` routes by channel.
- Routes (auth):
  - `POST /v1/subscriptions` `{area_id, product_id|null, kinds: [...], channels: ["inbox","email"], email?}` → 201.
    Validation: email is required when the email channel is chosen; at most 20 subscriptions per reporter.
  - `GET /v1/subscriptions` and `DELETE /v1/subscriptions/{id}` (soft deactivate).
  - `GET /v1/alerts?cursor=&limit=20` → `{items: [{id, kind, title, body, data, created_at, read_at}], next_cursor}`.
    The cursor is opaque base64 of `(created_at, id)`.
  - `POST /v1/alerts/{id}/read` → 204.
  - `GET /v1/alerts/unread_count` → `{count}` (used for the badge).

**Tests.**
- Unit: quiet hours 22:00–07:00 Asia/Dhaka. 23:30 local → deferred to 07:00 local; 12:00 → immediate; a window not
  crossing midnight (13:00–14:00) works.
- Unit: the render templates contain the product name, the percentage and both prices.
- E2E: an egg-gouging mini scenario with 2 subscribers (one for eggs, one for all products) → each gets exactly one
  inbox alert; a second anomaly.detected within 6 h → no new alerts (dedup); an email arrives in Mailpit (integration
  test queries the Mailpit API at `http://localhost:8025/api/v1/messages`).
- E2E: a failing notifier for one subscriber doesn't stop the other.
- API: cursor pagination returns stable, non-overlapping pages; you can't read another reporter's alert (404).

**Definition of done.** `make check && make test-int`. Manual: subscribe with an email, run `make sim SCENARIO=egg_gouging`
against the dev stack, and see the alert in Mailpit and in `GET /v1/alerts`.

**Out of scope.** Web push delivery (M21), the digest (M17).

---

### M17 — Daily rollups, maintenance, digest & scheduler

**Goal.** Time-based jobs (rollups, maintenance, half-life tuning, the daily digest, the outbox sweep and the deferred
alerts flush), all triggered through `daily.tick` events, so the same handlers run locally (APScheduler) and on AWS
(EventBridge Scheduler).

**Depends on.** M16

**Deliverables.**
- `workers/scheduler.py` — `spm scheduler` runs **APScheduler** (AsyncIOScheduler). For each job it publishes a
  `daily.tick` event and does no work itself:

| Job (`data.job`) | Schedule | Per area? |
|---|---|---|
| `outbox_sweep` | every 1 min | no |
| `deferred_alerts` | every 1 min | no |
| `rollup` | 00:15 area-local | yes |
| `maintenance` | 03:00 area-local | no |
| `digest` | 07:00 area-local | yes |

- `workers/jobs.py` — consumer group `jobs` dispatching on `data.job`:
  - **rollup(area, for_date = yesterday local)**: for every product, recompute that local day's `price_stats_daily`
    (area-level plus per shop) from `report_assessments` with the included labels and day boundaries in the area's
    timezone, and set `final = true`. Recompute `band_state` for every product (catches time-decay with no new reports)
    and push it to Redis.
  - **maintenance**:
    1. `spm_ensure_report_partitions(2)`.
    2. Expire PENDING reports older than their corroboration window → `EXPIRED`.
    3. Auto-resolve anomalies:
       - SHOP_OUTLIER, once the shop price is back within `|z_market| ≤ Z_RESOLVE_NORMAL`.
       - any anomaly older than `ANOMALY_AUTO_RESOLVE_D`.
       - MARKET_SHIFT after 7 days, also clearing `rebase_at`.
    4. Half-life tuning: for products with ≥ `HALF_LIFE_MIN_HISTORY_D` days of area-level p50s, compute
       `half_life_from_volatility`, limit the change to ±`HALF_LIFE_MAX_DAILY_CHANGE`, and update `products.half_life_h`
       (log old → new; the catalog cache picks it up within 5 minutes).
    5. Privacy: null `reports.location` for rows with `received_at < now − LOCATION_RETENTION_D` (keeping `distance_to_shop_m` and `location_verified`).
    6. Delete `processed_events` older than 14 days and published outbox rows older than 7 days.
  - **digest(area, for_date = today local)** via `core/digest.py`:
    - `build_digest(series_by_product, bands, shop_prices, anomalies_24h) -> DigestContent`:
      - top movers: the 3 products with the largest `|p50_today / p50_7d_ago − 1|`.
      - stale items: confidence STALE or `newest_obs_at` older than 2 half-lives.
      - best-value shop per product: the cheapest verified shop price.
      - anomalies in the last 24 h.
      - totals: reports yesterday, active reporters.
    - Store it in `digests` (upsert on `(area_id, day)`) and create inbox alerts (kind `DIGEST`) for subscribers with
      `DIGEST` in their kinds, plus email via the notifier (respecting quiet hours is unnecessary, since it runs at 07:00).
  - **outbox_sweep** → `OutboxSweeper.sweep()`; **deferred_alerts** → `flush_due(now)`.
- `GET /v1/digest/latest?area=` → the latest digest content (public).
- `entrypoints/lambda_jobs.py` — a Lambda handler accepting EventBridge Scheduler payloads shaped as the `daily.tick`
  envelope that calls the same dispatcher.

**Tests.**
- Unit: `build_digest` on fixture data → the correct top movers ordering, stale detection and best shop.
- Integration: rollup day boundaries in Asia/Dhaka (UTC+6). A report at 2026-10-09T19:30Z belongs to local day 2026-10-10.
- Integration: maintenance expires a PENDING report whose window passed; resolves a SHOP_OUTLIER once the shop price
  normalizes; nulls old locations; partition creation is idempotent.
- Integration: the half-life change is clamped (e.g. 48 → max 72 in one night).
- E2E: publishing a `daily.tick{job:"digest"}` creates a digest row and inbox alerts for DIGEST subscribers.

**Definition of done.** `make check && make test-int`; `make dev` runs the scheduler and the 1-minute jobs appear in the logs.

**Out of scope.** CloudWatch wiring (M18/M22).

---

### M18 — Metrics & observability

**Goal.** Measure what the project claims (price gaps detected, freshness, potential savings, data quality, latency),
with structured logs locally and CloudWatch EMF on AWS, plus an admin stats endpoint.

**Depends on.** M17

**Deliverables.**
- Metrics adapters:
  - `LogMetrics` (`SPM_METRICS=log`): batches and logs one JSON line per flush (every 10 s or 100 points).
  - `EmfMetrics` (`SPM_METRICS=emf`): writes **CloudWatch Embedded Metric Format** JSON to stdout. Namespace `SPM`,
    dimensions as given, and a flush at the end of each Lambda invocation. This costs no API calls.
- Metric catalog (emit exactly these names):

| Metric | Type | Dimensions | Emitted in |
|---|---|---|---|
| `reports_received` | count | `product` | M08 |
| `reports_rejected` | count | `code` | M08 |
| `reports_scored` | count | `classification` | M13 |
| `scoring_ms` | timing | — | M13 |
| `anomalies_opened` | count | `kind` | M13 |
| `alerts_sent` / `alerts_suppressed` | count | `channel` / `reason` | M16 |
| `checks` | count | `verdict` | M15 (via consumer) |
| `check_ms` | timing | — | M15 |
| `potential_savings` | count (sum) | `product` | metrics consumer |
| `band_age_hours` | gauge | `product` | M13 (band.updated) and M17 rollup |
| `fresh_products_pct` | gauge | `area` | M17 rollup |
| `outbox_lag_s` | gauge | — | outbox sweep |
| `dlq_depth` | gauge | — | M17 maintenance (Redis XLEN; on AWS, via SQS metrics) |

- `workers/metrics_consumer.py` — consumer group `metrics`:
  - `price.checked` → insert the `price_checks` row (`PriceCheckRepo.add`) + `checks{verdict}` + `potential_savings`
    (when the verdict ∈ {HIGH, SUSPICIOUS} and a cheaper option existed: `(quoted − cheapest) × base_quantity`).
  - `anomaly.detected` → `anomalies_opened{kind}`.
  - `band.updated` → `band_age_hours`.
- Structured logging review: every log line has `request_id` or `correlation_id`, `event_id` (in consumers) and
  `duration_ms` for request and handler completion. Add a request access log middleware (method, path template,
  status, duration). Never log JWTs, emails or raw coordinates.
- `GET /v1/admin/stats?area=&days=7` (admin key), computed with SQL:
  ```json
  {
    "window_days": 7,
    "reports": {"received": 812, "rejected_by_code": {"TOO_FAR_FROM_SHOP": 14}, "by_classification": {"NORMAL": 760, "PENDING": 12}},
    "price_gaps_detected": {"SHOP_OUTLIER": 3, "MARKET_SHIFT": 1},
    "freshness": {"median_report_age_h_by_product": {"onion": 3.1}, "fresh_products_pct": 90},
    "savings": {"checks": 420, "potential_savings_total": 3150.0, "median_saving_pct": 9.5},
    "data_quality": {"bad_report_rate": 0.031, "pending_confirmed_rate": 0.62, "active_reporters": 37}
  }
  ```
- Optional: an `/admin` page in the frontend (M21) that reads this endpoint.

**Tests.**
- Unit: the EMF output is valid JSON with `_aws.CloudWatchMetrics` and the correct namespace, dimensions and units.
- Unit: the potential-savings computation (multiplied by base quantity; zero when there is no cheaper option).
- Integration: after a short simulator run, `/v1/admin/stats` numbers match direct SQL counts.
- A log-redaction test: the `Authorization` header and emails don't appear in captured logs.

**Definition of done.** `make check && make test-int`; `make sim SCENARIO=onion_shock` followed by `/v1/admin/stats` shows non-zero gaps and savings.

**Out of scope.** The CloudWatch dashboard definition (M22).

---

### M19 — PWA foundation + Price Check screen (A)

**Goal.** The installable React PWA shell with device registration, a typed API client, the core UI components and
the main screen, **Price Check**.

**Depends on.** M15

**Deliverables.**
- `frontend/` scaffold: Vite + React 18 + TS (strict), Tailwind, React Router, TanStack Query, `vite-plugin-pwa`
  (manifest: name "Fair Price", short name "FairPrice", theme color, icons 192/512, `display: standalone`), ESLint and Prettier.
- `src/api/`:
  - `schema.d.ts`, generated from the backend OpenAPI (`pnpm gen:api` → `openapi-typescript http://localhost:8000/openapi.json`).
  - `client.ts`, a fetch wrapper that adds the bearer token, parses problem+json into a typed `ApiError {code, status, detail, hints?, suggestions?}`,
    and retries once on 401 by re-registering the device.
- `src/device/identity.ts` — on first launch, generate `device_id` (crypto.randomUUID) → `POST /v1/auth/device` → store
  `{device_id, token, reporter_id, expires_at}` in IndexedDB (`idb`, db `spm`, store `kv`). Refresh when < 7 days remain.
- `src/settings/` — the selected area (default from `/v1/areas`), a "use my location" toggle, currency display.
- Data hooks (TanStack Query): `useProducts` (staleTime 24 h, persisted), `useShops(area, coords)`,
  `useCheck(params)` (enabled on submit).
- Query cache persistence to IndexedDB (`@tanstack/query-persist-client-core` + idb), so the last bands and products
  work offline.
- Components (accessible: labels, roles, focus states, 44 px touch targets, color never the only signal):
  - `ProductPicker` — search over names **and aliases** (client-side, using `useProducts`), recent picks first.
  - `QuantityUnitInput` — preset chips from `unit_presets` plus custom quantity and unit; shows the normalized
    "≈ 80.00 / kg" live (duplicating the normalizer math client-side for preview only; the server is authoritative).
  - `ShopPicker` — the nearest shops first when location is allowed, with distance; otherwise alphabetical; optional for Check.
  - `VerdictCard` — a large verdict with icon and text (GOOD_DEAL green, FAIR neutral, HIGH amber, SUSPICIOUS red,
    UNKNOWN grey), the message, the fair band as a horizontal range bar with a marker for the quoted price, the
    confidence and `FreshnessBadge`.
  - `FreshnessBadge` — "2 h ago", turning to "stale" styling when `stale`.
  - `CheaperOptions` — a list with shop, distance, price per base unit, saving for the quantity and a verified tick.
  - `OfflineBanner` — shown when `navigator.onLine` is false, saying that the data is from <time>.
- Routes and layout: bottom navigation **Check · Report · Compare · Alerts**. `/` = Check; the other tabs are
  placeholders until M20/M21.
- **Price Check flow**: pick a product → enter price + quantity/unit → (optional shop) → "Check" → VerdictCard + CheaperOptions.
  If a `422 UNKNOWN_PRODUCT` comes back, show the suggestions as chips.
- Dev proxy: Vite `server.proxy['/v1'] → http://localhost:8000`.

**Tests (Vitest + Testing Library).**
- `ProductPicker` finds "Onion" when typing "piyaz".
- `QuantityUnitInput` preview: 40 for 500 g → "80.00 / kg"; 12 for 1 piece → "144.00 / dozen".
- `VerdictCard` renders every verdict with text (not only color) and puts the marker in the correct position on the range bar.
- The API client maps a problem+json into `ApiError` and re-registers on 401 exactly once.
- A Check page integration test with MSW (Mock Service Worker) mocking `/v1/check`.

**Definition of done.** `pnpm lint && pnpm test && pnpm build`; Lighthouse PWA "installable" passes; manual: with
`make dev`, the Check flow against seeded/simulated data shows a verdict on a phone-sized viewport (375 px) with no
horizontal scroll.

**Out of scope.** Reporting and offline writes (M20).

---

### M20 — Report a Price + offline queue

**Goal.** The report flow with GPS, **offline-first** submission through an IndexedDB outbox, idempotent sync,
and fixing rejected reports.

**Depends on.** M19

**Deliverables.**
- `src/offline/queue.ts` — an IndexedDB store `report_outbox` with records
  `{client_report_id, payload, created_at, attempts, next_attempt_at, status: "queued"|"sending"|"needs_attention"|"sent", last_error?, server_report_id?}`.
  - `enqueue(payload)` always writes locally first, then triggers `sync()`.
  - `sync()` takes a single-flight lock (Web Locks API if available, else an in-memory flag) and processes queued items
    whose `next_attempt_at ≤ now`, oldest first:
    - `202/200` → `sent` (store `server_report_id`).
    - `422` → `needs_attention` (store the problem with hints).
    - `401` → re-register, then retry.
    - `429` → respect `Retry-After`.
    - network or `5xx` → backoff `min(5 s × 2^attempts, 10 min)` with ±20% jitter.
  - Triggers: app start, the `online` event, `visibilitychange` → visible, every 30 s while visible, and the service
    worker Background Sync tag `report-sync` where supported (the SW posts a message to the clients; if no client is
    open, the SW itself can run the same logic via a shared module).
  - Sent items older than 7 days are pruned.
- **Report page** (`/report`):
  - Product (ProductPicker) → shop (ShopPicker, nearest first; "I'm at this shop" uses GPS) → price + QuantityUnitInput
    → observed time (defaults to now; "earlier today" picker within 7 days) → Submit.
  - Location: request `getCurrentPosition({enableHighAccuracy: true, timeout: 8000, maximumAge: 60000})`. Send
    `accuracy_m`. If denied, submit without location and show a note that verified-location reports count more.
  - A client-side pre-check shows a warning (not a block) if the normalized price is outside the product's
    `abs_min`/`abs_max` (add `abs_min`/`abs_max` to `/v1/products` if they're not there yet; a small backend change, noted).
  - After submit: a toast "Saved — will sync when online" or "Sent ✓".
- **My reports** list (section on the Report page): the queue items with status chips. `needs_attention` items show
  the server's message and hints as one-tap fixes (e.g. "Change to 500 g"), which edit the payload, reset attempts
  and re-queue with the **same** `client_report_id`.
  - For `sent` items, poll `GET /v1/reports/{id}` (up to 3 times, 10 s apart, while the page is open) and show
    `pending review`, `verified ✓` or `flagged`.
- Service worker (`vite-plugin-pwa` injectManifest mode, `src/sw.ts`): precache the app shell; runtime caching
  `StaleWhileRevalidate` for `/v1/products|areas|shops` and `NetworkFirst` (3 s timeout) for `/v1/check|compare|trends`;
  **never** cache POSTs.

**Tests.**
- Queue unit tests with `fake-indexeddb` and a mocked fetch: offline enqueue → stays queued; going online sends it;
  a 500 response backs off; a 422 moves it to needs_attention; a fix-and-resend keeps the same client_report_id;
  two concurrent `sync()` calls send each item once.
- An E2E test against the real backend (Playwright, optional but recommended): enable offline in the browser context →
  submit → go online → the backend has exactly 1 report.

**Definition of done.** `pnpm lint && pnpm test && pnpm build`; manual: in Chrome DevTools, go offline, submit 3
reports, go online, and see 3 reports in the backend with no duplicates.

**Out of scope.** Receipt photos (M24).

---

### M21 — Compare & Trends + Alerts inbox (+ web push)

**Goal.** The remaining screens, plus web push delivery.

**Depends on.** M20, M16

**Deliverables.**
- **Compare** (`/compare/:productId?`): a product picker, then the band summary card, then the shops list from `/v1/compare`
  sorted by price. Each row shows the price per base unit, the % vs the median (colored and labelled), a verified tick,
  a freshness badge, the distance, and an anomaly pill ("38% above area" / "deal"). A tap opens Trends.
- **Trends** (`/trends/:productId`): a Recharts area band (p25–p75) with a p50 line over 7/30/90 days (segmented control),
  anomaly markers (triangle up/down, with a tooltip of kind and magnitude), and change_7d/30d chips. Gaps for days
  with no data. It must be readable in both light and dark themes.
- **Alerts** (`/alerts`): an inbox list (cursor pagination, infinite scroll) with unread styling; a tap marks it read
  and navigates to the related product or shop; the unread badge on the nav tab (polls `unread_count` every 60 s while visible).
  - A **subscriptions** sub-page: create (area, product or "all", kinds checkboxes, channels: inbox always, email, push)
    and list/delete.
- **Web push**:
  - Backend:
    - `WebPushNotifier` using `pywebpush` with VAPID from settings.
    - The subscription stores the `webpush` JSON.
    - `GET /v1/push/vapid_public_key`.
    - On HTTP 404/410 from the push service, deactivate the webpush channel of that subscription.
  - Frontend: the "Enable push" button → `Notification.requestPermission` → `registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey})` → included in the subscription create/update.
    `sw.ts` `push` handler → `showNotification(title, {body, data:{url}})`; `notificationclick` → focus or open the URL.
- Optional: an `/admin` page that renders `/v1/admin/stats` (behind an admin key prompt, kept in memory only).

**Tests.**
- Compare: sorting, null prices last, anomaly pill text.
- Trends: renders with gaps; marker count equals the anomalies; the range switch refetches.
- Alerts: pagination merges pages without duplicates; mark-read updates the badge.
- Backend: `WebPushNotifier` deactivates on 410 (mock pywebpush).

**Definition of done.** `pnpm lint && pnpm test && pnpm build`, plus the backend `make check`. Manual: with `make dev`
and `make sim SCENARIO=egg_gouging`, an alert appears in the inbox and as a push notification (Chrome desktop).

**Out of scope.** Native apps.

---

### M22 — AWS infrastructure (CDK)

**Goal.** Deploy the same code to AWS: API Gateway + Lambda, RDS PostgreSQL/PostGIS, ElastiCache Redis,
EventBridge + SQS (+ DLQs), SNS, EventBridge Scheduler, CloudWatch dashboard and alarms, and the PWA on S3 + CloudFront.

**Depends on.** M18, M21

**Deliverables.** `infra/` (CDK v2 Python app, `cdk.json`, `app.py`), with stacks parameterized by `stage` (`dev`, `prod`):

| Stack | Resources |
|---|---|
| `SpmNetwork` | VPC with 2 AZs: public and private-with-egress subnets. For egress, a **single NAT instance** (e.g. the `fck-nat` AMI, t4g.nano) instead of NAT gateways, to keep student costs low (documented trade-off). Gateway endpoint for S3. Security groups: lambda-sg, db-sg (5432 from lambda-sg), cache-sg (6379 from lambda-sg). |
| `SpmData` | RDS PostgreSQL 16, `db.t4g.micro`, 20 GB gp3, single-AZ for dev, automated backups 7 days, deletion protection in prod, credentials in Secrets Manager. ElastiCache Redis (or Valkey) `cache.t4g.micro`, single node. A `migrate` Lambda (runs `alembic upgrade head` + `spm seed`), invoked by the deploy pipeline. |
| `SpmEvents` | EventBridge bus `spm-bus-{stage}`. One SQS standard queue per consumer group (`scoring`, `alerts`, `metrics`, `jobs`), each with a DLQ (`maxReceiveCount 5`), visibility timeout 6 × the Lambda timeout. Rules: `detail-type = price.reported` → scoring; `anomaly.detected` → alerts **and** metrics; `band.updated`, `price.checked` → metrics; `daily.tick` → jobs. |
| `SpmCompute` | Lambdas (Python 3.12, **arm64**, packaged with dependencies built for `manylinux2014_aarch64`): `api` (Mangum, 512 MB, 10 s, reserved concurrency 20), `scoring` / `alerts` / `metrics` / `jobs` (SQS event sources, batch 10, `ReportBatchItemFailures`, reserved concurrency 5 for scoring). All in private subnets with env vars from §2.6 (`SPM_EVENT_BUS=eventbridge`, `SPM_METRICS=emf`, `SPM_DB_POOL_SIZE=1`) and secrets from Secrets Manager at cold start. **HTTP API** (API Gateway v2) with default route throttling (rate 50, burst 100) and CORS for the CloudFront domain. |
| `SpmDelivery` | SNS topic `spm-alerts-{stage}` (email subscriptions use filter policies on `area`/`product`/`kind`). EventBridge Scheduler schedules (with the area timezone via `scheduleExpressionTimezone`) for each job in M17, targeting the bus with a `daily.tick` detail. |
| `SpmWeb` | S3 bucket (private) + CloudFront with OAC, SPA fallback to `index.html`, `/v1/*` behavior proxied to the HTTP API origin (so it's same-origin, with no CORS in prod), cache policy disabled for `/v1/*`. Optional AWS WAF web ACL on CloudFront (rate-based rule, AWS managed common rules), off by default because of cost. |
| `SpmObservability` | CloudWatch dashboard `SPM-{stage}`: reports received/rejected, classifications, anomalies, alerts, checks by verdict, potential savings, `check_ms` p95, `scoring_ms` p95, Lambda errors and throttles, SQS age of oldest message, DLQ depth, RDS CPU/connections. Alarms (to an ops SNS topic): any DLQ depth > 0, scoring queue age > 5 min, API 5xx > 1% over 5 min, `check_ms` p95 > 50 ms over 15 min. Log retention 14 days. |

- Notes to implement and document:
  - **Why the WAF is on CloudFront:** HTTP APIs don't support WAF directly; CloudFront in front gives WAF, same-origin and caching.
  - **Lambda ↔ RDS connections:** pool size 1 per container plus the reserved concurrency caps keep connections ≤ ~35.
    RDS Proxy is documented as the upgrade path.
  - **Cost note** in `infra/README.md`: list each always-on component (RDS, ElastiCache, NAT instance, optional WAF)
    and tell the reader to check current prices in the AWS Pricing Calculator. Don't hard-code numbers.
  - A `cdk-nag` (AwsSolutions pack) check runs in synth, with justified suppressions only.

**Tests.**
- `pytest infra/tests` with CDK assertions: queues have DLQs with maxReceiveCount 5; the Lambdas are arm64 and in
  private subnets; the DB isn't publicly accessible; the event rules route each detail-type to the correct queues;
  the scheduler timezone is set; the bucket blocks public access.
- `cdk synth` is clean (cdk-nag passes).

**Definition of done.** `cdk deploy --all -c stage=dev` succeeds. After `migrate`:
- the CloudFront URL loads the PWA.
- a report submitted from a phone gets scored (the classification is visible).
- `make sim SCENARIO=egg_gouging MODE=http BASE_URL=…` (with backfill enabled for dev) produces an alert email via SNS.
- the dashboard shows the metrics.

Record the deployed URLs and any manual steps in the notes.

**Out of scope.** A custom domain (optional), multi-region.

---

### M23 — CI/CD, load testing, hardening & demo

**Goal.** Automate quality and deployment, prove the latency budgets, close the security gaps, and package the project
for demonstration.

**Depends on.** M22

**Deliverables.**
- **CI** (`.github/workflows/ci.yml`), on PRs and main:
  - Backend: uv cache → `make lint typecheck test` → integration and e2e with GitHub service containers (`postgis/postgis:16-3.4`, `redis:7`) → coverage report.
  - Frontend: pnpm cache → lint, test, build.
  - Infra: `cdk synth` + infra tests.
  - Simulator: the mini target tests (M14).
- **CD** (`.github/workflows/deploy.yml`): manual dispatch or a tag `v*`, using **GitHub OIDC** → an AWS role (no
  long-lived keys). Steps:
  1. Build the Lambda artifacts.
  2. `cdk deploy --all -c stage=<stage> --require-approval never`.
  3. Invoke the `migrate` Lambda.
  4. Build the frontend → `aws s3 sync` → CloudFront invalidation.
  5. Smoke tests (`tools/smoke.py`): healthz, a device token, a report → poll until scored, a check.
  6. Require manual approval for `prod` (GitHub environment protection).
- **Load tests** (`tools/load/`, k6):
  - `check.js`: ramp to 50 RPS for 3 min on `/v1/check` with random products and prices. A custom metric parses
    `Server-Timing` for app time, with thresholds app p95 < 50 ms and an error rate < 0.5%.
  - `reports.js`: 10 RPS from 200 virtual devices for 3 min, threshold app p95 < 100 ms. After the run, scoring queue
    age returns to 0 within 2 min.
  - Results summarized in `doc/evaluation/load-test.md` (both local and AWS dev).
- **Hardening checklist** (each item verified and ticked in `doc/development/notes/M23.md`):
  - [ ] Input limits: body 16 KB, string lengths (product_text ≤ 64), numeric ranges, pagination limit ≤ 50.
  - [ ] Auth: JWT `iss`/`exp` validated, the secret ≥ 32 bytes from Secrets Manager, rotation procedure documented.
  - [ ] Rate limits on auth, reports and check (add `check` 120/min per reporter or IP).
  - [ ] CORS locked to the app origin; security headers on CloudFront (HSTS, CSP, X-Content-Type-Options, Referrer-Policy).
  - [ ] No PII in logs (test from M18); IP stored only as a salted hash; locations nulled after 30 days (M17).
  - [ ] Admin endpoints are disabled unless `SPM_ADMIN_API_KEY` is set; backfill is disabled in prod.
  - [ ] Dependency audit: `uv pip audit` / `pip-audit` and `pnpm audit` in CI (non-blocking warnings, blocking criticals).
  - [ ] Least-privilege IAM per Lambda (the scoring Lambda can't publish to SNS, etc.).
  - [ ] Backups: an RDS snapshot restore procedure documented and tried once on dev.
  - [ ] DLQ redrive procedure documented (`aws sqs start-message-move-task`).
- **Demo package**:
  - `doc/demo/DEMO.md`: a 5-minute script (seed → simulate a shock → show the verdict, alert and dashboard), with screenshots.
  - The root README updated with architecture, features A–G, how to run locally, how to deploy, and the evaluation results (M14) and load-test results.
  - `make demo`: resets the local DB, seeds, runs `onion_shock` + `egg_gouging` in-process, then starts the stack, so the UI has rich data.

**Definition of done.** CI is green on main; a deploy to `dev` via the workflow succeeds, including the smoke tests;
the load-test thresholds pass on local and are reported for AWS dev; every hardening item is ticked.

**Out of scope.** Production traffic.

---

### M24 — *(optional)* Receipt photos

**Goal.** Optional receipt photos as stronger evidence (a trust bonus), without slowing the write path.

**Depends on.** M20

**Deliverables.**
- `POST /v1/uploads/receipt` → a presigned S3 PUT URL (content-type `image/jpeg|png|webp`, max 2 MB, key
  `receipts/{reporter}/{uuid}.{ext}`, expiry 5 min). Locally use **MinIO** in docker-compose with the same S3 API.
- The client compresses the image (max 1600 px, quality 0.7) before upload, then sends `receipt_key` in `POST /v1/reports`.
- The guardrails verify that the key belongs to the reporter and the object exists (`HEAD`).
- The trust engine uses weight factor `× 1.25` when a receipt is attached (constant `RECEIPT_WEIGHT_BONUS`, capped so the weight ≤ 1.0).
- An S3 lifecycle rule deletes receipts after 90 days.
- No OCR in this module. OCR is a possible future upgrade.

**Tests.** Presign validation (type and size), foreign-key rejection, the weight bonus is applied and capped.

---

### M25 — *(optional)* ML upgrade behind the classifier interface

**Goal.** Once there is ≥ 90 days of data, add a learned baseline without changing the contracts.

**Depends on.** M14

**Deliverables.**
- A `Baseline` protocol in core: `expected(product, area, shop, at) -> (center, scale)`. The current robust
  median/MAD is `RobustBaseline`. Add `SeasonalBaseline` (STL decomposition on daily p50s, weekly seasonality)
  and/or an Isolation Forest score as an extra feature.
- The classifier takes the baseline as a dependency, selected by a setting (`SPM_BASELINE=robust|seasonal`).
- Training runs as a nightly job (or a SageMaker processing job on AWS). The model artifacts go to S3/local disk, versioned.
- **Gate:** the new baseline is enabled only if the M14 targets improve on the same scenarios plus a replay of real
  data, and the comparison is written to `doc/evaluation/ml-upgrade.md`.

---

## 6. Decision log

Every deviation from this document is recorded here: date, module, decision and reason.

| Date | Module | Decision | Reason |
|---|---|---|---|
| 2026-10-10 | Architecture | PostgreSQL + PostGIS instead of DynamoDB | geo "nearby" queries, cross-shop aggregates and medians are relational and analytical |
| 2026-10-10 | Architecture | Robust statistics (median/MAD) before ML | works with little data, explainable, no infrastructure cost; ML gated behind M25 |
| 2026-10-10 | Plan | Transactional outbox for every event | a report must never be stored without eventually being scored |
| 2026-10-10 | Plan | Raw `reports` immutable; mutable `report_assessments` | keeps the append-only audit trail while allowing PENDING → SHOP_OUTLIER/BAD_REPORT promotion |
| 2026-10-10 | Plan | Device-based anonymous auth (JWT HS256) instead of Cognito | zero-friction for the MVP; Cognito / phone OTP can replace it later behind the same `current_reporter` dependency |
| 2026-10-10 | Plan | HTTP API + CloudFront (WAF optional on CloudFront) | HTTP APIs are cheaper and simpler; WAF attaches to CloudFront |
| 2026-10-10 | Plan | Verdicts extended with GOOD_DEAL and UNKNOWN | honest "no recent data" answer; cheap prices aren't "fair" warnings |
| 2026-10-10 | Plan | Seed currency, coordinates and prices are placeholders | replace with real local shops and prices in M04 |
| 2026-10-19 | M00 | Empty `spm/core/__init__.py` created in M00 | import-linter needs the `spm.core` package to exist for its contract to run |
| 2026-10-19 | M00 | Dependency lower bounds in `pyproject.toml`, exact pins in `uv.lock` | uv default; the lockfile is the source of exact versions |
| 2026-10-22 | M01 | Added `BAD_REQUEST`, `METHOD_NOT_ALLOWED`, `PAYLOAD_TOO_LARGE`, `SERVICE_UNAVAILABLE` to the §2.5 registry | M01 must return problems for 400/405/413/503, and the registry had no codes for them |
| 2026-10-22 | M01 | Base `SpmError` created in M01 (`core/errors.py`); subclasses stay in M02 | the M01 exception handler needs the base class |
| 2026-10-22 | M01 | Unhandled exceptions are caught by `UnhandledErrorMiddleware` (inside RequestID), not by an `Exception` handler | Starlette runs `Exception` handlers outside all user middleware, so the 500 would lose `X-Request-ID`, CORS headers and the log context |
| 2026-10-22 | M01 | `/readyz` probes the URLs in settings with one-shot connections; an empty URL means "not configured" | the container has no DB/Redis clients until M03/M07; switch to the pooled clients then |
| 2026-10-22 | M01 | `UuidV7Generator` lives in `spm/adapters/ids.py` | needs `uuid-utils` (stdlib has no UUIDv7 on 3.12), so it can't live in pure `core` |
| 2026-10-22 | M02 | Enum members without an explicit value use their name as the value (`Verdict.HIGH = "HIGH"`, `Dimension.MASS = "MASS"`) | matches the M03 Postgres enum types and the M15 JSON (`"verdict": "HIGH"`) |
| 2026-10-22 | M02 | `FixedClock` stays in `core/clock.py` (M01); `adapters/memory` re-exports it | one implementation; M01 placed it in core |
| 2026-10-22 | M02 | `Event` (§3.1 envelope dataclass), `DailyStat` (M17 fields) and `OutboxRepo` (M07 signatures) are defined in full now; `SubscriptionRepo`, `AlertRepo` and `PriceCheckRepo` are empty placeholder Protocols | their shapes are already specified; the others aren't yet |
| 2026-10-22 | M02 | `ERROR_TYPES` tuple in `core/errors.py`; `RateLimited` takes `retry_after_s` and the API sets `Retry-After` from it | single source for the code → class mapping; §2.5 says `RATE_LIMITED` sets `Retry-After` |
| 2026-10-22 | M02 | mypy strict also covers `spm.ports`; mypy now checks `tests/`; a second import-linter contract keeps `spm.ports` free of adapters, frameworks and config | ports are pure interfaces, and adapter-vs-port conformance is checked statically in tests |
| 2026-10-22 | M03 | The DEFAULT partition is named `reports_default`; `spm_ensure_report_partitions` returns the number created, takes an advisory lock, and moves rows already in `reports_default` into a newly created month before attaching it | attaching a partition fails if the default partition holds rows for that range; the lock serializes the migration and the nightly job |
| 2026-10-22 | M03 | `0001_initial` runs the DDL one statement at a time; the downgrade keeps the `postgis` extension | asyncpg prepares each statement, so it rejects multi-statement strings; the extension is shared and created by `db-init.sql` |
| 2026-10-22 | M03 | `tables.py` is checked against the migrated DB by an Alembic autogenerate diff (integration test) | the migration is the source of truth; the diff stops the metadata drifting |
| 2026-10-22 | M03 | The container builds the engine and session factory when `SPM_DATABASE_URL` is set; `/readyz` uses the pooled engine for `db` | replaces the M01 one-shot DB probe, as planned in M01 |
| 2026-10-22 | M03 | Test databases are cloned from `template_postgis` when it exists | `CREATE EXTENSION postgis` in an empty DB takes about 4 s; cloning takes about 1 s |
| 2026-10-22 | M04 | `normalize_text` implemented now in `core/normalizer.py` (M05 adds the rest); it keeps Unicode combining marks | seeding stores normalized aliases; Bangla/Devanagari vowel signs are marks, and dropping them mangles words |
| 2026-10-22 | M04 | New port `ShopGeoIndex` (`load_shop_geo`, `shops_within`); `HotCache` extends it | M04 builds only the geo half of the hot cache; the API and seeder depend on the narrower port |
| 2026-10-22 | M04 | `CatalogCache.ensure_fresh()` is async, getters are sync; on a failed refresh the previous snapshot keeps being served | sync getters can't await I/O; a short DB blip shouldn't take the catalog down |
| 2026-10-22 | M04 | `spm:geo:shops` is rebuilt at API startup (best effort) as well as by `spm seed` | §3.2 says "rebuilt by seed and on startup" |
| 2026-10-22 | M04 | Seeding upserts and never deletes rows that were removed from the YAML | rows may be referenced by reports and subscriptions; set `active: false` to retire one |
| 2026-10-22 | M04 | `GET /v1/shops`: `radius_m` defaults to `NEARBY_RADIUS_M` (2000) and must be in (0, 50 000]; `lat`/`lng` must be given together; an unknown `area` is a 404; only active shops are returned; `/v1/products` lists active products only | the plan doesn't specify these cases |
| 2026-10-22 | M04 | `create_app(container=…)` accepts a pre-built container | API unit tests run on memory adapters without Docker |
| 2026-10-22 | M05 | Fuzzy score = `100 × (1 − d / (len(a) + len(b)))`, with `d` the unit-cost Levenshtein distance | the plan's required case "oniun" → onion (1 typo) scores 80 with the max-length ratio and with python-Levenshtein's `ratio`, below `FUZZY_MATCH_MIN_SCORE` = 85; this variant gives 90 |
| 2026-10-22 | M05 | New `FUZZY_MIN_LENGTH` = 5: shorter text never auto-resolves by fuzzy match (it still gets suggestions) | with the chosen ratio, one typo in a 4-letter word scores 87.5, so "chai" (tea) resolved to rice via "chal"; a wrong silent match is worse than a "did you mean" |
| 2026-10-22 | M05 | New `FUZZY_SUGGEST_MIN_SCORE` = 60 and `FUZZY_MAX_SUGGESTIONS` = 3 constants | the plan's literal values, moved into `constants.py` per §2.7 |
| 2026-10-22 | M05 | Non-positive or non-finite price/quantity and > 2 decimal places → `VALIDATION_ERROR` (with `fields`); missing product id and text → `VALIDATION_ERROR` | the plan says "validate" without naming a code; `QUANTITY_OUT_OF_RANGE` stays for the base-quantity bounds |
| 2026-10-22 | M05 | `NormalizedPrice.base_quantity` is unrounded (e.g. 0.08333333333 dozen); only `norm_price` is quantized | rounding to the column's 4 dp happens at storage (M08), so no precision is lost before the price division |
| 2026-10-22 | M06 | `QUANTITY_OUT_OF_RANGE` from step 2 also carries `hints` | the plan's own example (onion 40 for "500 kg") fails `normalize` on the 50 kg limit before the bounds step, so its "500 g" hint would never reach the user |
| 2026-10-22 | M06 | A hint must also normalize cleanly (base quantity in range); near-identical hints (prices within 1%) are merged, keeping the first; scaled quantities are rounded to 3 dp | a suggestion the user can't submit is useless, and "1 piece" and "0.083 dozen" are one suggestion |
| 2026-10-22 | M06 | `hints` in error extras are `{message, quantity, unit, norm_price}` with numbers; `problem()` runs extras through `jsonable_encoder` | the problem body is JSON; Decimals, datetimes and UUIDs in extras must serialize |
| 2026-10-22 | M06 | `RateLimiter.hit` returns `retry_after_s = 0` when allowed (both adapters); TTL via `EXPIRE … NX` in a `MULTI` | keeps the port contract from M02; NX sets the TTL on the first increment only |
| 2026-10-22 | M06 | A duplicate is reported in `Admission.duplicate_of`, not raised; M08 decides the response | the plan's `Admission` has `duplicate_of`; dedupe is a policy decision for the endpoint |
| 2026-10-22 | M06 | The guardrail service depends on narrow protocols (`ReportsUnitOfWork`, `BandLookup`); `hot_cache` is optional until M12, so hints use `sqrt(abs_min × abs_max)` as the reference | the full `UnitOfWork`/`HotCache` don't exist yet |
| 2026-10-22 | M06 | `find_recent_duplicate` also filters `received_at >= since − 5 min` | redundant for correctness (received ≥ observed − skew) but lets Postgres prune monthly partitions |

---

## 7. Glossary

| Term | Meaning |
|---|---|
| **Base unit** | The unit all prices are normalized to: kg, l or dozen |
| **Band / fair band** | The trust- and freshness-weighted P25–P75 range of included reports for one product in one area |
| **Half-life** | The time after which a report's weight halves; set per product by its volatility |
| **MAD** | Median absolute deviation, a robust spread measure; × 1.4826 ≈ standard deviation for normal data |
| **Robust z** | `(p − median) / (1.4826 × MAD_eff)` |
| **Corroborated** | Confirmed by an independent agreeing report within the window, or reported by a high-trust reporter |
| **Verified price** | A shop's price backed by ≥ 2 independent agreeing reports, or one high-trust report |
| **Re-base** | After a MARKET_SHIFT, the band uses only reports after the shift began |
| **Outbox** | A table of events written in the same transaction as the data, published after commit; guarantees delivery |
| **Hali** | A local count unit of 4 pieces (common for eggs) |
