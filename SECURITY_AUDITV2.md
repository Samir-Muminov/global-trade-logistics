SECURITY AUDIT V2 — Global Trade & Logistics Analytics Platform
Red Team Report · Phase 9
Scope: Everything added in Phases 4–8 (write API, webhooks, auth, Celery, analytics)
Methodology: OWASP Top 10 + Django-specific white-box analysis
Reference: Phase 3 SECURITY_AUDIT.md covered VULN-001 through VULN-010. Not repeated here.

New attack surface since Phase 3:
- POST /api/v1/shipments/create/ (booking endpoint)
- PATCH /api/v1/shipments/{id}/status/ (status transitions)
- POST /api/v1/shipments/{id}/cargo/ (cargo creation)
- POST /api/v1/shipments/{id}/events/ (tracking events)
- POST /api/v1/webhooks/carrier/{carrier_code}/ (HMAC webhook)
- POST /api/v1/auth/register/ (user registration)
- POST /api/v1/auth/token/ (JWT login)
- Celery task queue (Redis broker)
- Raw SQL in apps/logistics/analytics.py

Summary of new findings: 8 vulnerabilities (2 High, 4 Medium, 2 Low)
All Phase 3 Critical/High vulnerabilities: CLOSED (verified in tests/test_api/test_permissions.py)

---

VULN-011: Idempotency Key Collision — Race Condition on Concurrent Booking

📁 File: apps/api/v1/views_write.py
🔍 CTRL+F: _check_idempotency_key

ATTACK VECTOR:
Two concurrent POST /api/v1/shipments/create/ requests with the SAME
idempotency_key arrive simultaneously. Both call _check_idempotency_key()
before either has created the shipment. Both get None (key not found).
Both proceed to create a shipment. Result: two shipments with the same
idempotency_key in custom_attributes — booking is duplicated.

This requires true concurrency (two requests in-flight at the same millisecond)
which is rare but not impossible under load. A malicious client can deliberately
trigger this with a race condition attack using threading:

    import threading, requests
    def book(): requests.post("/api/v1/shipments/create/", json={..., "idempotency_key": "same-key"})
    threading.Thread(target=book).start()
    threading.Thread(target=book).start()

IMPACT:
Duplicate shipment records. Financial double-billing. Carrier assigned two
bookings for one cargo. Compliance records corrupted. High financial impact
in a logistics platform processing $10M/day.

SEVERITY: High

FIX:
📁 FILE: apps/logistics/migrations/0005_idempotency_unique_constraint.py
Create a migration adding a unique expression index on the idempotency key:

── WHAT TO ADD ──
Migration content (create this file):

from django.db import migrations

class Migration(migrations.Migration):
    atomic = False
    dependencies = [("logistics", "0004_add_user_fks")]
    operations = [
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
                idx_shipment_idempotency_key_unique
                ON shipments ((custom_attributes->>'idempotency_key'))
                WHERE custom_attributes ? 'idempotency_key';
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS idx_shipment_idempotency_key_unique;",
        ),
    ]

With a unique index, the second concurrent INSERT will fail with IntegrityError.
The existing retry loop in ShipmentCreateView catches IntegrityError — but only
for tracking_number collisions. Add explicit idempotency key handling:

📁 FILE: apps/api/v1/views_write.py
🔍 FIND: except IntegrityError:
── WHAT TO CHANGE ──
BEFORE:
            except IntegrityError:
                if attempt == max_retries - 1:
                    raise
                continue

AFTER:
            except IntegrityError as e:
                if "idx_shipment_idempotency_key_unique" in str(e):
                    existing = _check_idempotency_key(idempotency_key)
                    if existing:
                        return Response(
                            ShipmentListSerializer(existing).data,
                            status=status.HTTP_200_OK,
                        )
                if attempt == max_retries - 1:
                    raise
                continue

VERIFICATION:
def test_concurrent_idempotency_key_does_not_duplicate(db, shipper_user, booking_setup):
    import threading
    from apps.logistics.models import Shipment
    results = []
    def book():
        response = api_client.post("/api/v1/shipments/create/", payload, format="json")
        results.append(response.status_code)
    threads = [threading.Thread(target=book) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert Shipment.objects.filter(
        custom_attributes__idempotency_key=str(idempotency_key)
    ).count() == 1

STATUS: Fix documented. Migration 0005 required before production deploy.

---

VULN-012: Webhook Secret Exposure via Carrier API Response

📁 File: apps/logistics/models.py (Carrier.service_metadata)
🔍 CTRL+F: service_metadata

ATTACK VECTOR:
The carrier webhook secret is stored in service_metadata JSONB field:
    carrier.service_metadata = {"webhook_secret": "secret-msco-webhook-key-2025"}

seed_data.py sets these secrets in plaintext. If any API endpoint ever serializes
a Carrier object including service_metadata, the webhook secret is exposed.

Check CarrierLeaderboardSerializer — it serializes carrier_id and carrier_name
but not service_metadata directly. However, if a developer adds service_metadata
to any future serializer, all carrier secrets are exposed.

Additionally: the secret appears in seed_data.py as:
    "webhook_secret": f"secret-{code.lower()}-webhook-key-2025"
This is predictable. An attacker who knows the carrier code can guess the
secret used in dev/staging environments if seed data was used to bootstrap.

IMPACT:
Attacker can forge webhook requests from any carrier, inject arbitrary tracking
events, manipulate shipment status data, create false delivery confirmations.
In a financial logistics platform: fraudulent delivery confirmation = cargo theft.

SEVERITY: High

FIX:
📁 FILE: apps/logistics/management/commands/seed_data.py
🔍 FIND: "webhook_secret": f"secret-{code.lower()}-webhook-key-2025"

── WHAT TO CHANGE ──
BEFORE:
    "service_metadata": {
        "webhook_secret": f"secret-{code.lower()}-webhook-key-2025",
        "alliance": "2M" if code in ("MSCO", "MAEU") else "THE",
    },

AFTER:
    import secrets as _secrets
    "service_metadata": {
        "webhook_secret": _secrets.token_hex(32),
        "alliance": "2M" if code in ("MSCO", "MAEU") else "THE",
    },

Also add to CarrierSerializer (any serializer that exposes Carrier):

📁 FILE: apps/api/v1/serializers.py
🔍 FIND: class CarrierLeaderboardSerializer

── WHAT TO ADD (after existing fields) ──
Add explicit exclusion documentation:
    # service_metadata intentionally excluded — contains webhook_secret.
    # Never add service_metadata to any client-facing serializer.

VERIFICATION:
def test_carrier_leaderboard_does_not_expose_webhook_secret(api_client, shipper_user):
    CarrierFactory(service_metadata={"webhook_secret": "super-secret"})
    api_client.force_authenticate(user=shipper_user)
    response = api_client.get("/api/v1/analytics/carriers/leaderboard/")
    assert "webhook_secret" not in response.content.decode()
    assert "super-secret" not in response.content.decode()

STATUS: Partial fix applied (seed_data). Serializer documentation added.

---

VULN-013: django-axes Bypass via X-Forwarded-For Header Spoofing

📁 File: config/settings/base.py
🔍 CTRL+F: AXES_LOCKOUT_PARAMETERS

ATTACK VECTOR:
django-axes locks by ip_address extracted from request.META["REMOTE_ADDR"].
Behind a load balancer, REMOTE_ADDR is the proxy IP — not the client IP.
axes reads X-Forwarded-For if AXES_IPWARE_META_PRECEDENCE_ORDER is configured,
but by default it trusts the first IP in X-Forwarded-For which can be spoofed:

    POST /api/v1/auth/token/ HTTP/1.1
    X-Forwarded-For: 1.2.3.4
    {"email": "victim@example.com", "password": "attempt1"}

    POST /api/v1/auth/token/ HTTP/1.1
    X-Forwarded-For: 5.6.7.8
    {"email": "victim@example.com", "password": "attempt2"}

Each request appears to come from a different IP — axes never locks out.
AXES_FAILURE_LIMIT=5 is bypassed by rotating the spoofed X-Forwarded-For header.

IMPACT:
Brute force protection is completely bypassed. Attacker can attempt unlimited
password guesses against any email address. Combined with credential stuffing
lists, account takeover is feasible.

SEVERITY: Medium

FIX:
📁 FILE: config/settings/base.py
🔍 FIND: AXES_USERNAME_FORM_FIELD = "email"

── WHAT TO ADD ──
# Lock by username (email) ONLY — not by IP.
# Rationale: IP-based lockout is bypassable via X-Forwarded-For spoofing.
# Username-based lockout is not — the attacker must use the victim's email.
# Trade-off: username-only lockout allows DoS against known emails. Mitigated
# by AuthRateThrottle (5/minute per IP at DRF level) as the IP-based layer.
AXES_LOCKOUT_PARAMETERS = ["username"]  # was ["ip_address", "username"]

# Use the last IP in X-Forwarded-For (set by our trusted load balancer)
# instead of the first (which can be spoofed by clients).
AXES_IPWARE_META_PRECEDENCE_ORDER = ("HTTP_X_FORWARDED_FOR",)

VERIFICATION:
def test_axes_locks_on_username_not_ip(api_client, db):
    user = CustomUserFactory(email="target@example.com")
    for i in range(6):
        api_client.post(
            "/api/v1/auth/token/",
            {"email": "target@example.com", "password": "wrongpassword"},
            HTTP_X_FORWARDED_FOR=f"10.0.0.{i}",
        )
    response = api_client.post(
        "/api/v1/auth/token/",
        {"email": "target@example.com", "password": user.password},
        HTTP_X_FORWARDED_FOR="99.99.99.99",
    )
    assert response.status_code == 403  # locked, regardless of IP

STATUS: Fix documented. Apply to base.py settings.

---

VULN-014: Raw SQL in analytics.py — Parameter Type Confusion

📁 File: apps/logistics/analytics.py
🔍 CTRL+F: cursor.execute(sql, [str(days), limit])

ATTACK VECTOR:
top_lanes_by_volume() passes `days` and `limit` as parameters. The days
parameter is cast to string then used in an INTERVAL cast:
    (%s || ' days')::INTERVAL

If `days` is user-supplied and not validated before reaching this function,
an attacker could pass a value like:
    days = "1; SELECT pg_sleep(10)--"

The psycopg2/psycopg3 parameterized query (%s) correctly escapes the value
as a string literal — so this specific pattern is NOT vulnerable to SQL injection.

However: the function accepts `days: int` but does not enforce it:
    top_lanes_by_volume(limit=10, days="1 union select...")

Python's type hint is not enforced at runtime. If a future API endpoint
exposes this function directly without validation, SQL injection becomes possible.

IMPACT:
Currently: no SQL injection due to parameterized queries (verified).
Risk: future caller without input validation could introduce SQL injection.
Defense-in-depth requires runtime validation at the function boundary.

SEVERITY: Medium (currently safe, preventive hardening required)

FIX:
📁 FILE: apps/logistics/analytics.py
🔍 FIND: def top_lanes_by_volume(limit: int = 10, days: int = 90)

── WHAT TO CHANGE ──
BEFORE:
def top_lanes_by_volume(limit: int = 10, days: int = 90) -> list[dict]:

AFTER:
def top_lanes_by_volume(limit: int = 10, days: int = 90) -> list[dict]:
    if not isinstance(days, int) or days < 1 or days > 365:
        raise ValueError(f"days must be int between 1-365, got: {days!r}")
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise ValueError(f"limit must be int between 1-100, got: {limit!r}")

Apply the same pattern to percentile_transit_times() and exception_cascade_analysis().

VERIFICATION:
def test_top_lanes_rejects_string_days():
    with pytest.raises(ValueError):
        top_lanes_by_volume(days="1 union select 1--")

def test_top_lanes_rejects_out_of_range():
    with pytest.raises(ValueError):
        top_lanes_by_volume(days=99999)

STATUS: Fix documented. Apply to analytics.py.

---

VULN-015: Celery Task Queue Poisoning via Redis

📁 File: config/settings/base.py
🔍 CTRL+F: CELERY_BROKER_URL

ATTACK VECTOR:
CELERY_BROKER_URL = "redis://localhost:6379/0" has no password (no AUTH).
If Redis is exposed on the network (misconfigured firewall, Docker network
misconfiguration), an attacker can connect and push arbitrary task messages:

    redis-cli LPUSH celery '{"task": "apps.logistics.tasks.send_delay_notifications", "args": [], "kwargs": {"threshold_hours": -999999}}'

More critically, a poisoned task message with a crafted task name pointing
to a non-existent module can crash Celery workers:
    {"task": "os.system", "args": ["rm -rf /"], "kwargs": {}}

Note: Celery's registered task list prevents arbitrary function execution,
but crash-based DoS on worker pool is still possible.

IMPACT:
Denial of service on Celery worker pool. Delay notifications stop firing.
Materialized view refreshes stop. Analytics data becomes stale.
In worst case: if Redis AUTH is missing in production, attacker can
enumerate all task messages including webhook payloads in the queue.

SEVERITY: Medium

FIX:
📁 FILE: config/settings/production.py
🔍 FIND: CELERY_BROKER_URL

── WHAT TO CHANGE ──
BEFORE:
CELERY_BROKER_URL = "redis://localhost:6379/0"

AFTER:
# Redis with AUTH — password required. Use rediss:// for TLS in production.
# Railway Redis: use the REDIS_URL env var which includes the password.
CELERY_BROKER_URL = env("REDIS_URL")  # format: redis://:password@host:6379/0
CELERY_RESULT_BACKEND = env("REDIS_URL")

Also add task rate limits to prevent queue flooding:

CELERY_TASK_ANNOTATIONS = {
    "apps.logistics.tasks.refresh_materialized_views_task": {"rate_limit": "4/m"},
    "apps.logistics.tasks.populate_snapshots_task": {"rate_limit": "2/m"},
}

VERIFICATION:
Verify Redis requires AUTH:
    redis-cli -h your-redis-host ping
    # Should return: NOAUTH Authentication required.

STATUS: Fix documented. Apply to production.py.

---

VULN-016: JWT Claims Not Verified in Permission Classes

📁 File: apps/users/permissions.py
🔍 CTRL+F: company_id = getattr(user, "company_id", None)

ATTACK VECTOR:
MyTokenObtainPairSerializer adds company_id and carrier_id to JWT payload.
However, IsShipmentOwnerOrStaff reads company_id from request.user.company_id
(the DB-backed attribute), NOT from the JWT token claims.

This means the permission check always hits the database — the JWT claims
are never actually used for authorization. The JWT optimization documented
in users/serializers.py is not realized.

More importantly: if a user's company changes after token issuance (e.g. they
are reassigned to a different company), the token still carries the old
company_id in claims — but the DB check uses the current company_id.
This is actually CORRECT behavior for security (DB is authoritative),
but the documentation in serializers.py is misleading — it claims DB queries
are eliminated when they are not.

IMPACT:
No security vulnerability — DB-backed permission checks are correct.
Documentation misleads future developers into thinking JWT claims are
used for authorization, potentially causing them to switch to claim-based
checks (which would be a vulnerability if company changes are not reflected).

SEVERITY: Low (documentation/architecture clarity issue)

FIX:
📁 FILE: apps/users/serializers.py
🔍 FIND: This eliminates DB queries in permission checks

── WHAT TO CHANGE ──
BEFORE:
    # This eliminates DB queries in permission checks — the permission class
    # reads from the token instead of hitting the DB for every request.

AFTER:
    # These claims are available for future client-side use (e.g. showing
    # company name without an API call). Permission checks still use the DB
    # (request.user.company_id) for authorization — DB is always authoritative.
    # Do NOT use JWT claims for authorization decisions.

STATUS: Documentation fix only. No code change required.

---

VULN-017: process_unmatched_webhook Task — Unbounded Retry Loop

📁 File: apps/logistics/tasks.py
🔍 CTRL+F: raise self.retry(

ATTACK VECTOR:
process_unmatched_webhook retries up to max_retries=5 times when shipment
is not found. Each retry is delayed by exponential backoff. However:

1. A carrier sends a webhook for a shipment that will NEVER exist (typo in
   tracking number, or test webhook from carrier's staging environment).
2. Task retries 5 times, each time failing to find the shipment.
3. After 5 retries, task raises MaxRetriesExceededError — goes to dead letter queue.
4. If dead letter queue is not monitored, these pile up silently.
5. At 100 such webhooks/hour: 500 Celery task slots consumed, worker pool saturated.

IMPACT:
Celery worker pool DoS via unresolvable webhook flood from carrier test environments.
Legitimate tasks (MV refresh, snapshot population) are delayed or dropped.

SEVERITY: Medium

FIX:
📁 FILE: apps/logistics/tasks.py
🔍 FIND: def process_unmatched_webhook(

── WHAT TO CHANGE ──
Add a circuit breaker: after max_retries, log to a dedicated unmatched webhook
log instead of crashing. Also add a time-based expiry — do not retry a webhook
older than 24 hours.

BEFORE:
    if shipment is None:
        raise self.retry(
            countdown=30 * (2 ** self.request.retries),
            exc=ValueError(f"Shipment {shipment_reference} not found"),
        )

AFTER:
    if shipment is None:
        from datetime import datetime
        received = datetime.fromisoformat(received_at)
        age_hours = (timezone.now() - received).total_seconds() / 3600
        if age_hours > 24:
            logger.error(
                "Webhook %s expired after 24h without matching shipment %s. "
                "Discarding — check carrier EDI configuration.",
                webhook_id, shipment_reference,
            )
            return {"webhook_id": webhook_id, "status": "expired"}

        raise self.retry(
            countdown=30 * (2 ** self.request.retries),
            exc=ValueError(f"Shipment {shipment_reference} not found"),
        )

VERIFICATION:
def test_expired_webhook_does_not_retry(db):
    from datetime import timedelta
    old_time = (timezone.now() - timedelta(hours=25)).isoformat()
    result = process_unmatched_webhook.delay(
        webhook_id="evt_expired",
        carrier_code="TESTSCR",
        shipment_reference="GT-NONEXISTENT",
        event_type="test",
        payload={},
        received_at=old_time,
    )
    data = result.get()
    assert data["status"] == "expired"

STATUS: Fix documented. Apply to tasks.py.

---

VULN-018: populate_carrier_snapshots Memory Leak on Large Carrier Count

📁 File: apps/logistics/analytics.py
🔍 CTRL+F: for carrier_id in carriers:

ATTACK VECTOR:
populate_carrier_snapshots() loops over ALL active carriers and for each
carrier executes 1 aggregate query + 3 update_or_create() calls.
The carrier IDs list is fetched with values_list() into Python memory first.

At 500 carriers this is 2000 DB round-trips in a single task execution.
Each round-trip creates a new DB cursor, executes, fetches, closes.
Under a memory-constrained Celery worker (512MB RAM limit on Railway free tier),
this loop combined with Django's DB query logging accumulates:
- 500 QuerySet result objects
- 500 * 3 = 1500 AnalyticsSnapshot Django ORM instances in memory
- Django's db.reset_queries() is not called between iterations

With CONN_MAX_AGE=60, connections are reused — but if the task runs longer
than 60 seconds (possible at 500 carriers), connections may be recycled
mid-task causing OperationalError.

IMPACT:
Celery worker OOM crash at scale. populate_snapshots beat task fails silently.
Analytics data stops updating. Dashboard shows stale data.

SEVERITY: Low (only manifests at 500+ carriers, which is our stated scale target)

FIX:
📁 FILE: apps/logistics/analytics.py
🔍 FIND: for carrier_id in carriers:

── WHAT TO CHANGE ──
BEFORE:
    carriers = Carrier.objects.filter(is_active=True).values_list("id", flat=True)
    upserted = 0

    for carrier_id in carriers:

AFTER:
    from django.db import reset_queries
    carriers = list(Carrier.objects.filter(is_active=True).values_list("id", flat=True))
    upserted = 0

    for i, carrier_id in enumerate(carriers):
        # Reset accumulated query log every 50 iterations to prevent memory growth.
        # Django accumulates queries in django.db.connection.queries when DEBUG=True.
        # In production DEBUG=False so this is free — but safe to call regardless.
        if i % 50 == 0 and i > 0:
            reset_queries()

VERIFICATION:
With 500 carriers and DEBUG=True, measure memory before and after:
    import tracemalloc
    tracemalloc.start()
    populate_carrier_snapshots(date.today())
    current, peak = tracemalloc.get_traced_memory()
    assert peak < 100 * 1024 * 1024  # peak < 100MB

STATUS: Fix documented. Apply to analytics.py.

---

Summary Table

| ID       | Title                                        | Severity | Status     | Phase 3 Ref |
|----------|----------------------------------------------|----------|------------|-------------|
| VULN-011 | Idempotency key race condition               | High     | Fix documented | New     |
| VULN-012 | Webhook secret exposure via API response     | High     | Partial fix applied | New |
| VULN-013 | django-axes bypass via XFF spoofing          | Medium   | Fix documented | New     |
| VULN-014 | Raw SQL parameter type confusion             | Medium   | Fix documented | New     |
| VULN-015 | Celery task queue poisoning via Redis        | Medium   | Fix documented | New     |
| VULN-016 | JWT claims not used in permission checks     | Low      | Doc fix only | New      |
| VULN-017 | Unbounded retry loop on unmatched webhooks   | Medium   | Fix documented | New     |
| VULN-018 | Memory leak in snapshot population loop      | Low      | Fix documented | New     |

Phase 3 vulnerabilities VULN-001 through VULN-010: all verified closed.
See tests/test_api/test_permissions.py and tests/test_api/test_shipments_write.py.

No new Critical vulnerabilities found in Phases 4–8 surface.
Highest severity: High (2 findings — VULN-011, VULN-012).
VULN-011 requires migration 0005 before production deploy.
VULN-012 requires seed_data.py fix before staging environments are shared.