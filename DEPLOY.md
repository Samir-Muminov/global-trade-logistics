DEPLOYMENT CHECKLIST — Global Trade & Logistics Analytics Platform
Every item is verifiable with a shell command. Run top to bottom before going live.

---

PRE-DEPLOY: Local Verification

[ ] 1. All tests pass
    pytest tests/ -q
    Expected: 118+ passed, 0 failed

[ ] 2. No Django system check issues
    python manage.py check --deploy
    Expected: System check identified no issues

[ ] 3. No pending migrations
    python manage.py migrate --check
    Expected: No migrations to apply

[ ] 4. Requirements are pinned
    grep -E "==[0-9]" requirements.txt | wc -l
    Expected: same number as total lines in requirements.txt

[ ] 5. No secrets in codebase
    grep -r "SECRET_KEY\|password\|webhook_secret" --include="*.py" \
      --exclude-dir=".git" --exclude-dir="migrations" --exclude="test_*.py" \
      --exclude="seed_data.py" .
    Expected: only settings references to env vars, no hardcoded values

---

RAILWAY: Environment Variables

Set these in Railway Dashboard > Project > Variables before first deploy.

[ ] 6. DJANGO_SECRET_KEY is set
    railway variables | grep DJANGO_SECRET_KEY
    Generate: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

[ ] 7. DJANGO_SETTINGS_MODULE is set
    railway variables | grep DJANGO_SETTINGS_MODULE
    Expected value: config.settings.production

[ ] 8. DATABASE_URL is set (Railway PostgreSQL)
    railway variables | grep DATABASE_URL
    Expected format: postgresql://user:pass@host:5432/dbname

[ ] 9. REDIS_URL is set (Railway Redis)
    railway variables | grep REDIS_URL
    Expected format: redis://:password@host:6379/0

[ ] 10. ALLOWED_HOSTS is set
    railway variables | grep ALLOWED_HOSTS
    Expected value: yourapp.railway.app (or custom domain)

[ ] 11. SENTRY_DSN is set
    railway variables | grep SENTRY_DSN
    Get from: sentry.io > Project > Settings > SDK Setup

[ ] 12. FRONTEND_URL is set
    railway variables | grep FRONTEND_URL
    Expected value: https://yourapp.railway.app or custom domain

[ ] 13. DEFAULT_FROM_EMAIL is set
    railway variables | grep DEFAULT_FROM_EMAIL
    Expected value: noreply@yourdomain.com

---

RAILWAY: Database

[ ] 14. PostgreSQL extensions are active
    railway run psql $DATABASE_URL -c "\dx"
    Expected: pg_trgm, btree_gin, uuid-ossp listed

    If missing:
    railway run psql $DATABASE_URL -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
    railway run psql $DATABASE_URL -c "CREATE EXTENSION IF NOT EXISTS btree_gin;"
    railway run psql $DATABASE_URL -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'

[ ] 15. All migrations applied
    railway run python manage.py migrate --check
    Expected: No migrations to apply

[ ] 16. Materialized views exist
    railway run psql $DATABASE_URL -c "\dm"
    Expected: mv_carrier_daily_stats, mv_route_monthly_performance, mv_port_congestion

---

RAILWAY: First Deploy

[ ] 17. Trigger deploy
    git push origin main
    Watch: Railway Dashboard > Deployments > Build logs

[ ] 18. Build completes without errors
    Look for in build logs:
    - "pip install" succeeded
    - "collectstatic" succeeded
    - "migrate" succeeded with no errors

[ ] 19. Health check passes
    curl -s https://YOUR_DOMAIN/api/v1/health/ | python -m json.tool
    Expected:
    {
      "status": "ok" or "degraded",
      "db": "ok",
      "cache": "ok",
      "version": "1.0.0"
    }
    HTTP 200 required. "db" must be "ok".

---

RAILWAY: Post-Deploy Verification

[ ] 20. API returns 401 for unauthenticated requests
    curl -s -o /dev/null -w "%{http_code}" https://YOUR_DOMAIN/api/v1/shipments/
    Expected: 401

[ ] 21. Auth endpoint is alive
    curl -s -X POST https://YOUR_DOMAIN/api/v1/auth/token/ \
      -H "Content-Type: application/json" \
      -d '{"email":"wrong@test.com","password":"wrong"}' | python -m json.tool
    Expected: 401 with {"detail": "No active account..."}

[ ] 22. Swagger UI is accessible
    curl -s -o /dev/null -w "%{http_code}" https://YOUR_DOMAIN/api/docs/
    Expected: 200

[ ] 23. Static files are served with compression
    curl -sI https://YOUR_DOMAIN/static/admin/css/base.css | grep -i content-encoding
    Expected: content-encoding: gzip

[ ] 24. Admin panel loads
    curl -s -o /dev/null -w "%{http_code}" https://YOUR_DOMAIN/admin/
    Expected: 302 (redirect to login)

---

RAILWAY: Celery Worker

[ ] 25. Celery worker service is deployed
    Deploy a second Railway service using railway.worker.json as the config.
    Start command: celery -A config.celery worker --loglevel=info --concurrency=2

[ ] 26. Celery worker connects to Redis
    Check Celery worker logs in Railway:
    Expected: "Connected to redis://..."
    Expected: "celery@hostname ready"

[ ] 27. Health check shows Celery status
    curl -s https://YOUR_DOMAIN/api/v1/health/ | python -m json.tool
    Expected: "celery": "ok" (when worker is running)

---

SENTRY: Error Tracking

[ ] 28. Sentry receives a test event
    railway run python -c "
    import sentry_sdk
    sentry_sdk.init(dsn='$SENTRY_DSN')
    sentry_sdk.capture_message('Deployment verification — Phase 10')
    "
    Check Sentry dashboard for the event within 60 seconds.

---

ROLLBACK PROCEDURE

If deployment fails or causes errors:

1. Railway Dashboard > Deployments > click previous successful deploy > Redeploy
   OR
   railway rollback

2. If DB migration caused the issue:
   railway run python manage.py migrate logistics 0004
   (roll back to last known good migration)

3. Verify rollback:
   curl -s https://YOUR_DOMAIN/api/v1/health/
   Expected: HTTP 200, "db": "ok"

---

TROUBLESHOOTING

Error: "ModuleNotFoundError: No module named 'config.settings.production'"
Fix: Set DJANGO_SETTINGS_MODULE=config.settings.production in Railway variables

Error: "django.db.utils.OperationalError: could not connect to server"
Fix: Verify DATABASE_URL in Railway variables matches PostgreSQL service URL

Error: "ImproperlyConfigured: The SECRET_KEY setting must not be empty"
Fix: Set DJANGO_SECRET_KEY in Railway variables

Error: Static files returning 404
Fix: Verify MIDDLEWARE has WhiteNoiseMiddleware second (after SecurityMiddleware)
     Verify build log shows "collectstatic" completed successfully

Error: "400 Bad Request" on every request
Fix: Add your Railway domain to ALLOWED_HOSTS variable