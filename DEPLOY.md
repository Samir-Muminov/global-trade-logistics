# DEPLOY GUIDE — Global Trade & Logistics Analytics Platform
## Railway Production Deployment

Reproducible by any team member. No assumed knowledge beyond basic CLI usage.

---

## Prerequisites

- Python 3.13+ installed locally
- Git installed
- GitHub account
- Railway account (free tier works): https://railway.app
- PostgreSQL 17 running locally (for development)

Verify local setup:
```bash
python --version    # must be 3.13+
git --version
psql --version      # must be 17.x
```

---

## 1. Local Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/global-trade-logistics.git
cd global-trade-logistics

# Install dependencies
pip install -r requirements-dev.txt

# Create local database
psql -U postgres -c "CREATE DATABASE global_trade;"
psql -U postgres -c "CREATE USER global_trade_user WITH PASSWORD 'postgres123';"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE global_trade TO global_trade_user;"
psql -U postgres -c "GRANT ALL ON SCHEMA public TO global_trade_user;"

# Run migrations
python manage.py migrate

# Verify everything is clean
python manage.py check
```

Expected output: `System check identified no issues (0 silenced).`

---

## 2. Environment Variables

These go into the **Railway dashboard** under your project → Variables tab.
Never commit these to Git.

| Variable | Example Value | Purpose | Breaks If Missing |
|----------|--------------|---------|-------------------|
| `DJANGO_SECRET_KEY` | `a3f8...` (50 random chars) | Signs sessions, CSRF tokens, JWT | `ImproperlyConfigured` on startup |
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` | Tells Django which settings to use | Uses dev settings in production |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db` | PostgreSQL connection string | `OperationalError` on first DB query |
| `ALLOWED_HOSTS` | `yourapp.railway.app` | Validates HTTP Host header | `400 Bad Request` on every request |
| `REDIS_URL` | `redis://default:pass@host:6379` | Cache backend for analytics endpoints | Falls back to LocMemCache (single-process only) |

Generate a secure `DJANGO_SECRET_KEY`:
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## 3. Database Setup (Railway PostgreSQL)

Railway provides managed PostgreSQL. Add it to your project:

```
Railway Dashboard → Your Project → + New → Database → PostgreSQL
```

Once provisioned, Railway auto-creates `DATABASE_URL` in your project variables.
Copy it — you'll see it in the Variables tab as `DATABASE_URL`.

**Enable required PostgreSQL extensions** (run once after first deploy):
```bash
# Connect to Railway PostgreSQL via Railway CLI
railway run psql $DATABASE_URL -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
railway run psql $DATABASE_URL -c "CREATE EXTENSION IF NOT EXISTS btree_gin;"
railway run psql $DATABASE_URL -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
```

These extensions are required by the GIN indexes in `0001_initial.py`.
The migration handles this automatically via `RunSQL` — but verify manually
if you see index creation errors in deploy logs.

---

## 4. First Deploy

**Step 1 — Connect GitHub to Railway:**
```
Railway Dashboard → New Project → Deploy from GitHub repo → Select your repo
```

**Step 2 — Set environment variables** (see section 2 above):
```
Project → Variables → Add each variable
```

**Step 3 — Set the start command:**
Railway reads `railway.json` automatically. The start command is:
```
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

**Step 4 — Trigger deploy:**
```bash
git add .
git commit -m "deploy(3.1): initial Railway production deploy"
git push origin main
```

Railway auto-deploys on push to `main`. Watch the build logs in the Railway dashboard.

**Step 5 — Run migrations on first deploy:**
Railway runs `python manage.py migrate` via `railway.json` buildCommand.
Verify in build logs: look for `Running migrations: Applying logistics.0001_initial... OK`

---

## 5. Verify Deploy

Replace `YOUR_DOMAIN` with your Railway-provided URL (format: `yourapp.railway.app`).

**Check 1 — API is reachable and returns JSON (not HTML error page):**
```bash
curl -s https://YOUR_DOMAIN/api/v1/shipments/ \
  -H "Accept: application/json" | python -m json.tool
```
Expected: `{"detail": "Authentication credentials were not provided."}` with HTTP 401.
This confirms: Django is running, URL routing works, DRF is active.

**Check 2 — Auth endpoint is alive:**
```bash
curl -s -X POST https://YOUR_DOMAIN/api/v1/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "wrong"}' | python -m json.tool
```
Expected: `{"detail": "No active account found with the given credentials"}` with HTTP 401.
This confirms: JWT auth endpoint is wired up correctly.

**Check 3 — Static files served correctly (WhiteNoise):**
```bash
curl -I https://YOUR_DOMAIN/static/admin/css/base.css
```
Expected: HTTP 200 with `Content-Encoding: gzip` header.
This confirms: WhiteNoise is serving compressed static files correctly.

---

## 6. Custom Domain (Optional)

```
Railway Dashboard → Your Service → Settings → Domains → Add Custom Domain
```

Add your domain, then update `ALLOWED_HOSTS` in Railway Variables:
```
ALLOWED_HOSTS=yourapp.railway.app,yourdomain.com
```

Railway provides free TLS certificates via Let's Encrypt automatically.

---

## Rollback Procedure

**When to use:** production is returning 500s after a deploy, and you need to
restore the previous working version immediately.

**Via Railway dashboard:**
```
Railway Dashboard → Deployments → click previous successful deploy → Redeploy
```

**Via Railway CLI:**
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# List deployments
railway deployments

# Rollback to specific deployment
railway rollback DEPLOYMENT_ID
```

**After rollback:** investigate the failed deploy in build logs, fix the issue
in a branch, re-run CI, then merge to main only when CI is green.

---

## Commit Convention

All commits in this project follow Conventional Commits format.
German and Swedish clients read commit history — this signals discipline.

```
feat(phase-1): add fortress DB models with constraints and indexes
fix(bug-003): resolve circular import in querysets.py using apps.get_model()
perf(phase-2): add composite partial index for active carrier+ETA queries
security(phase-3): close IDOR vulnerability on ShipmentDetailView
deploy(3.1): add Railway config and production settings
test(phase-3): add coverage for permission classes and throttling
```

Format: `type(scope): description`

Types: `feat`, `fix`, `perf`, `security`, `deploy`, `test`, `docs`, `refactor`

---

## Troubleshooting

### Error 1: `ModuleNotFoundError: No module named 'config.settings.production'`

**Cause:** `DJANGO_SETTINGS_MODULE` env var is set to `config.settings.production`
but the file doesn't exist, or `config/settings/` is not a Python package
(missing `__init__.py`).

**Fix:**
```bash
# Verify the file exists
ls config/settings/
# Must show: __init__.py  base.py  production.py

# If __init__.py is missing:
echo. > config/settings/__init__.py  # Windows
touch config/settings/__init__.py    # Linux/Mac
```

---

### Error 2: `django.db.utils.OperationalError: could not connect to server`

**Cause:** `DATABASE_URL` is wrong, PostgreSQL extensions not installed,
or Railway PostgreSQL service is not linked to the web service.

**Fix:**
```
Railway Dashboard → Your Web Service → Variables
```
Verify `DATABASE_URL` is present and points to your Railway PostgreSQL instance.
If you added PostgreSQL after the web service, you may need to redeploy.

---

### Error 3: `django.core.exceptions.ImproperlyConfigured: The SECRET_KEY setting must not be empty`

**Cause:** `DJANGO_SECRET_KEY` environment variable is not set in Railway.

**Fix:**
```bash
# Generate a key
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
# Add the output to Railway Variables as DJANGO_SECRET_KEY
```

---

### Error 4: Static files returning 404 (`/static/admin/css/base.css` → 404)

**Cause:** `collectstatic` did not run during build, or `STATIC_ROOT` is wrong.

**Fix:** Check Railway build logs for `collectstatic` output.
If missing, verify `railway.json` buildCommand includes:
```
python manage.py collectstatic --noinput
```
Also verify `whitenoise.middleware.WhiteNoiseMiddleware` is second in `MIDDLEWARE`
(immediately after `SecurityMiddleware`).

---

### Error 5: `400 Bad Request` on every request

**Cause:** `ALLOWED_HOSTS` does not include your Railway domain.

**Fix:**
```
Railway Variables → ALLOWED_HOSTS → set to: yourapp.railway.app
```
After saving, Railway auto-redeploys. The 400 will stop immediately.