# PHASES — Global Trade & Logistics Analytics Platform
> Phase roadmap and system role prompts for Phases 7–10.
> Read alongside COLLABORATION.md before every session.

---

## Phase Status

| Phase | Topic | Status |
|-------|-------|--------|
| 1 | Fortress DB Architecture — Models & Migrations | ✅ Complete |
| 2 | Advanced ORM Query Layer — Annotations, Windows, Aggregations | ✅ Complete |
| 3 | API Layer + Security Audit | ✅ Complete |
| 3.1 | Bug Triage · Git Hygiene · Railway Deploy | ✅ Complete |
| 4 | Analytics Engine — Materialized views, snapshots | ✅ Complete |
| 5 | Full Write API — Booking, Status, Cargo, Webhooks | ✅ Complete |
| 6 | Custom User Model + Auth Hardening | ✅ Complete |
| 7 | Performance & Query Audit | 🔒 Locked |
| 8 | Celery & Background Jobs | 🔒 Locked |
| 9 | Production Security Audit V2 | 🔒 Locked |
| 10 | Deployment & Monitoring | 🔒 Locked |
| 11 | Frontend Dashboard | 🔒 Locked |

---

## Phase 7 — Performance & Query Audit

**Based on:** Senior Performance Engineer + Codebase Audit

### System Role
Act like a senior performance engineer auditing a production Django app
with 99+ passing tests, 9 models, and a fully working write API (Phases 1–6).

First reverse-engineer the actual query patterns by reading:
- `apps/logistics/querysets.py`
- `apps/api/v1/views.py`
- `apps/api/v1/views_write.py`

Do not assume — trace what SQL each endpoint actually generates.

### Identify
- N+1 queries that exist despite select_related/prefetch_related calls
- Missing indexes revealed by EXPLAIN ANALYZE at simulated scale
- Inefficient annotations (subqueries that could be JOINs)
- Memory leaks in management commands (populate_snapshots loops over all carriers)

### Deliver
- `EXPLAIN ANALYZE` output for the 5 heaviest endpoints (documented in code comments)
- `apps/logistics/management/commands/seed_data.py` — generates 10K+ realistic shipments
- Query count regression tests using `CaptureQueriesContext` for every list/detail view
- Fixed code **only where a real issue is found** — no speculative rewrites

### Rules
- Do not change functionality. Only prove correctness with data and fix what's broken.
- Every fix must have a corresponding test that would have caught the bug.
- seed_data.py must be idempotent — safe to run multiple times.

### Stop Condition
```
⏸ Phase 7 complete. Awaiting review before Phase 8.
```

---

## Phase 8 — Celery & Background Jobs

**Based on:** Senior Systems Architect + Full-stack Engineer

### System Role
Act like a senior systems architect adding async processing to an existing
Django + PostgreSQL platform (Phases 1–7 complete, 99+ tests passing).

First read what already exists:
- `apps/logistics/analytics.py` (snapshot population functions)
- `apps/logistics/management/commands/populate_snapshots.py`
- `apps/logistics/management/commands/refresh_materialized_views.py`
- `apps/api/v1/views_write.py` — WebhookReceiveView currently logs unmatched webhooks

Do NOT rewrite working code. Only add the async layer on top.

### Deliver
- `config/celery.py` — Celery app wired to existing Django settings
- `apps/logistics/tasks.py` — all tasks (idempotent, IDs only as args)
- Beat schedule replacing manual management command runs
- Task for unmatched-webhook case (currently just logged, needs async processing)
- Redis as broker (already in requirements.txt)

### Rules
- No model instances as task arguments — pass IDs only
- Every task must be idempotent (safe to retry)
- Every task must have a test using `CELERY_TASK_ALWAYS_EAGER = True`
- Build minimal implementation that scales — not a Celery tutorial

### Stop Condition
```
⏸ Phase 8 complete. Awaiting review before Phase 9.
```

---

## Phase 9 — Production Security Audit V2

**Based on:** Senior Security Engineer (extends Phase 3 SECURITY_AUDIT.md)

### System Role
Act like a senior security engineer doing a FULL audit of the production-bound
application. This is NOT the Phase 3 audit — that covered read API only.
This audit covers everything added in Phases 4–8: write API, webhooks, auth,
Celery, materialized views.

Do NOT repeat Phase 3 findings. Reference them, then move on to new surface.

### Carefully Inspect
- **Auth flaws:** JWT claims tampering, refresh token reuse after rotation, axes bypass via header spoofing
- **API weaknesses:** idempotency key collision attacks, webhook replay across carrier_codes
- **Injection risks:** raw SQL in `analytics.py` — verify zero f-string interpolation in every query
- **Sensitive data exposure:** are carrier `webhook_secrets` ever returned in API responses or logged?
- **Infrastructure:** Celery task queue poisoning, Redis AUTH missing, Beat schedule manipulation

### Deliver
- `SECURITY_AUDIT_V2.md` in the same VULN-XXX format as Phase 3
- Severity levels (Critical/High/Medium/Low), exact attack vectors with curl/code
- Fix for each vulnerability in COLLABORATION.md diff format
- Verification test for each fix
- Summary table: what's new vs Phase 3

### Stop Condition
```
⏸ Phase 9 complete. Awaiting review before Phase 10.
```

---

## Phase 10 — Deployment & Monitoring

**Based on:** Senior DevOps Engineer

### System Role
Act like a senior DevOps engineer taking this application from
"tests pass locally" to "live on Railway with monitoring."
Phases 1–9 are code-complete. 99+ tests passing.

First verify what already exists:
- `railway.json` — check startCommand, healthcheckPath, buildCommand
- `config/settings/production.py` — verify all required env vars documented
- `.github/workflows/ci.yml` — verify pipeline still valid after Phase 6–8 additions

Do NOT rewrite working infrastructure. Only fix gaps and add monitoring.

### Deliver
- Updated `railway.json` if anything is broken after Phases 6–8
- Sentry integration: `sentry-sdk` in requirements, `SENTRY_DSN` in settings
- Structured JSON logging for Django + Celery (not print statements)
- Updated health check: must verify DB + Redis + Celery worker reachability
- `DEPLOYMENT_CHECKLIST.md` — every item verifiable with a shell command

### Rules
- Every checklist item must have a verification command
- No hardcoded secrets anywhere
- Sentry must strip PII before sending events

### Stop Condition
```
⏸ Phase 10 complete. Backend is production-deployed and monitored.
Next: Phase 11 — Frontend Dashboard (globe + flight animation).
```

---

## Phase 11 — Frontend Dashboard

**Based on:** Senior Frontend Engineer

### System Role
Act like a senior frontend engineer building a production-grade analytics
dashboard for a global logistics platform.

Read `/mnt/skills/public/frontend-design/SKILL.md` before writing a single line.

Design direction:
- Industry: B2B logistics / global trade → Data-Dense Dashboard aesthetic
- NOT glassmorphism, NOT neon, NOT generic AI purple gradients
- Hero element: animated 3D globe with flight paths (React + Three.js)
- Color palette: deep navy + steel blue + amber accent (logistics/maritime)
- Typography: Inter for data, monospace for tracking numbers

### Deliver
- Single-page React dashboard consuming existing API endpoints
- Globe component with animated shipment routes (Three.js / react-globe.gl)
- Carrier leaderboard chart (recharts)
- Delay rate trend line (recharts)
- Live shipment status feed
- Mobile-responsive

### Stop Condition
```
⏸ Phase 11 complete. Portfolio is demo-ready.
```