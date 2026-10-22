# Smart Pricing Monitor

A crowdsourced fair-price checker for groceries. It answers one question quickly and honestly:

> **"Is the price I'm being quoted right now fair, and where could I get it cheaper?"**

People report shop prices from a PWA. Every report is normalized to a base unit (per kg / l / dozen), checked by
ingest guardrails, weighted by reporter trust and freshness, and classified so that a wrong report, an out-of-line
shop and a market-wide move are told apart before anyone is alerted.

- **Architecture:** [`doc/architecture/README.md`](doc/architecture/README.md) (visual: [`architecture.html`](doc/architecture/architecture.html))
- **Build plan:** [`doc/development/MODULES.md`](doc/development/MODULES.md) — the system is built module by module; module notes live in [`doc/development/notes/`](doc/development/notes/)

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2 (Docker Desktop, OrbStack or Colima)
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you if needed)
- Node.js 20+ and [pnpm](https://pnpm.io/) (for the PWA, from M19)
- GNU Make

## Quickstart

```bash
cp .env.example backend/.env   # then fill in the required secrets
make setup                     # backend deps, frontend deps (when present), pre-commit hooks
make up                        # Postgres+PostGIS :5433, Redis :6380, Mailpit :8025 / SMTP :1025
make check                     # lint + typecheck + unit tests
```

Run `make help` for all targets. Mailpit's UI is at <http://localhost:8025>.

## Repository layout

| Path | What |
|---|---|
| `backend/` | Python 3.12 service: FastAPI API, workers, domain core (`src/spm/`) and tests |
| `frontend/` | React + Vite PWA (from M19) |
| `infra/` | Local infra scripts and the AWS CDK app (M22) |
| `tools/simulator/` | Synthetic data and evaluation harness (M14) |
| `doc/` | Architecture and development docs |
