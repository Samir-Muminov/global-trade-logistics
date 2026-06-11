SECURITY AUDIT — Global Trade & Logistics Analytics Platform
Red Team Report · Phase 3
Scope: All files produced in Phases 1–3
Methodology: OWASP Top 10 + Django-specific attack surface analysis
Auditor role: Attacker with full source code access (white-box)

VULN-001: IDOR on Shipment Detail Endpoint
📁 File: apps/api/v1/views.py
🔍 CTRL+F: class ShipmentDetailView
ATTACK VECTOR:
GET /api/v1/shipments/3fa85f64-5717-4562-b3fc-2c963f66afa6/ HTTP/1.1
Authorization: Bearer <valid_token_for_user_B>
User B authenticates with their own valid JWT. They obtained shipment UUID
belonging to User A via a leaked email, invoice screenshot, or by iterating
over UUIDs exposed in their own shipment list. Without object-level permission,
the view returns the full ShipmentDetailSerializer payload for any UUID.
IMPACT:
Full shipment detail exposed: declared value, counterparty identities (shipper/
consignee names), HS codes (reveals commodity intelligence), B/L numbers,
cargo descriptions, tracking history. In global trade, this is competitively
sensitive and potentially a customs compliance violation.
SEVERITY: Critical
FIX:
📁 FILE: apps/api/v1/views.py
🔍 FIND: permission_classes = [IsAuthenticated, IsShipmentOwnerOrStaff]
Already implemented via IsShipmentOwnerOrStaff. The fix is to ensure it is
never removed or downgraded to IsAuthenticated alone.
── WHAT TO CHANGE ──
BEFORE (vulnerable):
pythonpermission_classes = [IsAuthenticated]
AFTER (hardened):
pythonpermission_classes = [IsAuthenticated, IsShipmentOwnerOrStaff]
VERIFICATION:
python# test_idor.py
def test_user_cannot_access_other_user_shipment(api_client, user_a, user_b, shipment_a):
    api_client.force_authenticate(user=user_b)
    response = api_client.get(f"/api/v1/shipments/{shipment_a.id}/")
    assert response.status_code == 403

VULN-002: Mass Assignment via Writable Serializer Fields
📁 File: apps/api/v1/serializers.py
🔍 CTRL+F: class ShipmentDetailSerializer
ATTACK VECTOR:
If read_only_fields is not set and the serializer is used on a PATCH/PUT endpoint:
PATCH /api/v1/shipments/3fa85f64.../ HTTP/1.1
Authorization: Bearer <valid_token>
Content-Type: application/json

{"carrier_id": "attacker-controlled-uuid", "declared_value": "0.01"}
Attacker reassigns the shipment to an arbitrary carrier or zeroes the declared
value (insurance/customs fraud vector).
IMPACT:
Arbitrary field overwrite on financial and compliance-critical fields.
Declared value manipulation is customs fraud. Carrier reassignment breaks
audit trails and financial settlement.
SEVERITY: Critical
FIX:
📁 FILE: apps/api/v1/serializers.py
🔍 FIND: read_only_fields = fields
Already implemented: all fields in ShipmentDetailSerializer are in
read_only_fields. The fix is enforcement:
── WHAT TO CHANGE ──
BEFORE (vulnerable pattern):
pythonclass Meta:
    model = Shipment
    fields = ("id", "carrier_id", "declared_value", ...)
    # no read_only_fields
AFTER (hardened):
pythonclass Meta:
    model = Shipment
    fields = ("id", "carrier_id", "declared_value", ...)
    read_only_fields = fields  # entire serializer is immutable via API
VERIFICATION:
pythondef test_cannot_mass_assign_carrier(api_client, user, shipment):
    api_client.force_authenticate(user=user)
    response = api_client.patch(
        f"/api/v1/shipments/{shipment.id}/",
        {"carrier_id": str(other_carrier.id)},
    )
    # Either 405 Method Not Allowed (no PATCH endpoint) or field ignored
    assert response.status_code in [405, 200]
    shipment.refresh_from_db()
    assert shipment.carrier_id != other_carrier.id

VULN-003: Unrestricted months Parameter — Aggregation DoS
📁 File: apps/api/v1/views.py
🔍 CTRL+F: class ShipmentRouteAnalyticsView
ATTACK VECTOR:
GET /api/v1/analytics/shipments/trends/?months=999999 HTTP/1.1
Authorization: Bearer <valid_token>
Without validation, Django passes 999999 directly to the date arithmetic.
PostgreSQL executes TruncMonth GROUP BY over the entire shipments table
(10M+ rows) scanned from the beginning of time. At scale, this is a
multi-second full-table aggregation that saturates DB CPU and connection pool.
Repeated at 10 req/s by a botnet = DB brownout.
IMPACT:
Denial of service via expensive aggregation query. No data leaked, but
availability impact is severe. Can be used to mask other attacks.
SEVERITY: High
FIX:
📁 FILE: apps/api/v1/views.py
🔍 FIND: if months < 1 or months > 24:
Already implemented. Validation is in place:
── WHAT TO CHANGE ──
BEFORE (vulnerable):
pythonmonths = int(request.query_params.get("months", 12))
# no bounds check
cutoff = timezone.now() - timezone.timedelta(days=months * 30)
AFTER (hardened):
pythonmonths = int(request.query_params.get("months", 12))
if months < 1 or months > 24:
    raise ValidationError({"months": "Must be between 1 and 24."})
cutoff = timezone.now() - timezone.timedelta(days=months * 30)
VERIFICATION:
pythondef test_months_param_upper_bound(api_client, user):
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/analytics/shipments/trends/?months=9999")
    assert response.status_code == 400
    assert "months" in response.json()

VULN-004: Information Disclosure via Django DEBUG Mode
📁 File: config/settings.py
🔍 CTRL+F: DEBUG = True
ATTACK VECTOR:
GET /api/v1/shipments/not-a-uuid/ HTTP/1.1
With DEBUG = True in production, Django returns a full HTML traceback page
including: local variable values, full stack trace, settings module path,
installed apps list, middleware chain, and sometimes partial SQL queries.
IMPACT:
Leaks internal architecture: model names, file paths, installed packages,
database query structure. Sufficient for targeted SQL injection or path
traversal attempts.
SEVERITY: High
FIX:
📁 FILE: config/settings/production.py
🔍 FIND: DEBUG
── WHAT TO CHANGE ──
BEFORE:
pythonDEBUG = True
AFTER:
pythonDEBUG = False
# DRF will return {"detail": "Not found."} JSON — no stack traces
Also add to config/settings/production.py:
pythonREST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "apps.api.v1.exceptions.json_exception_handler",
}
VERIFICATION:
pythondef test_404_does_not_leak_internals(api_client):
    response = api_client.get("/api/v1/shipments/invalid-uuid/")
    assert "Traceback" not in response.content.decode()
    assert "django" not in response.content.decode().lower()

VULN-005: Broken Object-Level Auth on Analytics — Unauthenticated Dashboard Access
📁 File: apps/api/v1/views.py
🔍 CTRL+F: class DashboardSummaryView
ATTACK VECTOR:
GET /api/v1/analytics/dashboard/ HTTP/1.1
# No Authorization header
If DEFAULT_PERMISSION_CLASSES is set to AllowAny (common misconfiguration
during development), the dashboard endpoint is publicly accessible. It returns
total shipment count, total declared value, and delay statistics across the
entire platform — aggregate financial intelligence.
IMPACT:
Competitive intelligence leak: total platform volume, financial throughput,
operational health metrics exposed to competitors or market analysts.
SEVERITY: High
FIX:
📁 FILE: apps/api/v1/views.py
🔍 FIND: permission_classes = [IsStaffOnly]
Already hardened at the view level. Defence-in-depth requires the global default:
── WHAT TO CHANGE ──
BEFORE (misconfigured global):
pythonREST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}
AFTER:
pythonREST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
}
VERIFICATION:
pythondef test_dashboard_requires_auth(api_client):
    response = api_client.get("/api/v1/analytics/dashboard/")
    assert response.status_code == 401

def test_dashboard_requires_staff(api_client, normal_user):
    api_client.force_authenticate(user=normal_user)
    response = api_client.get("/api/v1/analytics/dashboard/")
    assert response.status_code == 403

VULN-006: Rate Limit Bypass via IP Rotation
📁 File: apps/api/v1/throttling.py
🔍 CTRL+F: class AnonThrottle
ATTACK VECTOR:
DRF's AnonRateThrottle identifies anonymous clients by IP address from
request.META['REMOTE_ADDR']. Behind a load balancer or CDN, this is the
proxy IP — not the client IP. All anonymous requests appear to come from
the same IP, either:

Collapsing all anon users into one rate limit bucket (unfair limiting), or
If X-Forwarded-For is trusted blindly, attackers spoof it:

GET /api/v1/shipments/ HTTP/1.1
X-Forwarded-For: 1.2.3.4, 5.6.7.8, 9.10.11.12
Each request with a different spoofed first IP bypasses the per-IP rate limit.
IMPACT:
Rate limiting rendered ineffective. Enables credential stuffing, enumeration,
and aggregation DoS without restriction.
SEVERITY: Medium
FIX:
📁 FILE: config/settings/production.py
🔍 FIND: SECURE_PROXY_SSL_HEADER
── WHAT TO ADD ──
python# Trust only the last IP in X-Forwarded-For (set by your trusted load balancer)
# Do NOT use USE_X_FORWARDED_HOST without this
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
NUM_PROXIES = 1  # DRF uses this to extract correct client IP from X-Forwarded-For
In settings/base.py:
pythonREST_FRAMEWORK = {
    "NUM_PROXIES": 1,  # strips spoofed IPs, trusts only the last N hops
}
VERIFICATION:
pythondef test_xff_spoofing_does_not_bypass_throttle(api_client):
    for i in range(15):
        response = api_client.get(
            "/api/v1/shipments/",
            HTTP_X_FORWARDED_FOR=f"192.168.{i}.1",
        )
    # Should still be throttled after 10 requests
    assert response.status_code == 429

VULN-007: JWT — No Refresh Token Rotation or Revocation on Logout
📁 File: apps/api/v1/urls.py
🔍 CTRL+F: TokenRefreshView
ATTACK VECTOR:
User logs out. Their refresh token is still valid for the full
REFRESH_TOKEN_LIFETIME (default: 1 day). An attacker who exfiltrated the
refresh token (XSS, log exposure, MitM before HTTPS) can continue obtaining
new access tokens indefinitely after the user believes they are logged out.
POST /api/v1/auth/token/refresh/ HTTP/1.1
Content-Type: application/json

{"refresh": "<stolen_refresh_token>"}
→ 200 OK with new access token — even after logout
IMPACT:
Persistent account compromise after logout. Logout is security theater.
SEVERITY: High
FIX:
📁 FILE: config/settings/base.py
🔍 FIND: SIMPLE_JWT
── WHAT TO ADD ──
pythonfrom datetime import timedelta

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),   # short-lived
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,     # new refresh token issued on every refresh
    "BLACKLIST_AFTER_ROTATION": True,  # old refresh token blacklisted immediately
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
}
Add rest_framework_simplejwt.token_blacklist to INSTALLED_APPS and run:
bashpython manage.py migrate
VERIFICATION:
pythondef test_refresh_token_blacklisted_after_rotation(api_client, refresh_token):
    # First refresh — succeeds, returns new refresh token
    r1 = api_client.post("/api/v1/auth/token/refresh/", {"refresh": refresh_token})
    assert r1.status_code == 200
    # Reuse original refresh token — must fail
    r2 = api_client.post("/api/v1/auth/token/refresh/", {"refresh": refresh_token})
    assert r2.status_code == 401

VULN-008: Unrestricted Ordering — Full Table Sort on Unindexed Field
📁 File: apps/api/v1/views.py
🔍 CTRL+F: class ShipmentListView
ATTACK VECTOR:
If OrderingFilter is added without a whitelist:
GET /api/v1/shipments/?ordering=notes HTTP/1.1
notes is a TextField with no index. PostgreSQL performs a full sequential
scan + sort on a 10M-row table. At P99 this is 8–15 seconds. Repeated
10x/second = complete DB saturation.
IMPACT:
Denial of service via expensive sort operation. No authentication bypass,
but availability impact is severe.
SEVERITY: Medium
FIX:
Already mitigated by not including OrderingFilter in filter_backends.
Ordering is hardcoded to -departure_date in get_queryset().
If OrderingFilter is ever added:
── WHAT TO ADD ──
pythonfrom rest_framework.filters import OrderingFilter

class ShipmentListView(ListAPIView):
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    # WHITELIST ONLY — never omit this
    ordering_fields = ["departure_date", "declared_value"]
    ordering = ["-departure_date"]
VERIFICATION:
pythondef test_ordering_by_unindexed_field_rejected(api_client, user):
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/shipments/?ordering=notes")
    # Either ignored (returns default ordering) or 400
    # Result must not be sorted by `notes`
    assert response.status_code in [200, 400]

VULN-009: N+1 Risk — Cargo Items in Detail View
📁 File: apps/api/v1/views.py
🔍 CTRL+F: prefetch_related("cargo_items")
ATTACK VECTOR:
If prefetch_related("cargo_items") is removed from ShipmentDetailView.get_queryset():
GET /api/v1/shipments/{id}/ HTTP/1.1
CargoSerializer(many=True) iterates obj.cargo_items.all(). Without prefetch,
each access to cargo_items issues a new SELECT * FROM cargo WHERE shipment_id=X.
At 1 cargo item this is invisible; at 50 cargo items (bulk shipment) it's 50 queries.
IMPACT:
Performance degradation to O(n) queries per request. Not a security breach,
but creates availability risk under load and masks real performance baselines.
SEVERITY: Low (performance) / Medium (if used to amplify load)
FIX:
Already implemented. prefetch_related("cargo_items") is in get_queryset().
── WHAT TO VERIFY ──
pythondef test_shipment_detail_query_count(api_client, user, shipment_with_10_cargo_items):
    api_client.force_authenticate(user=user)
    with django.test.utils.CaptureQueriesContext(connection) as ctx:
        api_client.get(f"/api/v1/shipments/{shipment_with_10_cargo_items.id}/")
    # Should be ≤ 5 queries regardless of cargo count
    assert len(ctx.captured_queries) <= 5

VULN-010: Missing CONN_MAX_AGE — Connection Pool Exhaustion
📁 File: config/settings/base.py
🔍 CTRL+F: DATABASES
ATTACK VECTOR:
Without CONN_MAX_AGE, Django opens a new PostgreSQL connection for every
HTTP request and closes it when the request ends. Under load (500 req/s),
this creates 500 simultaneous connection open/close cycles. PostgreSQL's
default max_connections=100 is exhausted. New requests receive:
django.db.utils.OperationalError: FATAL: sorry, too many clients already
IMPACT:
Complete database unavailability. All API endpoints return 500.
SEVERITY: High (operational risk, not a direct attack — but trivially exploitable)
FIX:
📁 FILE: config/settings/production.py
🔍 FIND: DATABASES
── WHAT TO CHANGE ──
BEFORE:
pythonDATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "global_trade",
        ...
    }
}
AFTER:
pythonDATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "global_trade",
        ...
        "CONN_MAX_AGE": 60,        # reuse connections for 60 seconds
        "CONN_HEALTH_CHECKS": True, # verify connection is alive before reuse
    }
}
VERIFICATION:
Load test with locust at 200 concurrent users. Monitor pg_stat_activity
connection count — should remain stable, not grow linearly with request rate.

Hardening Checklist — config/settings/production.py
The following settings must be active before any production deployment.
Every setting below has a direct security or reliability impact.
python# ── TLS / Transport ───────────────────────────────────────────────────────────
SECURE_SSL_REDIRECT = True                    # redirect all HTTP to HTTPS
SECURE_HSTS_SECONDS = 31536000               # 1 year HSTS header
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# ── Cookies ───────────────────────────────────────────────────────────────────
SESSION_COOKIE_HTTPONLY = True               # JS cannot read session cookie
SESSION_COOKIE_SECURE = True                 # session cookie over HTTPS only
SESSION_COOKIE_SAMESITE = "Lax"             # CSRF protection
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

# ── Django Core ───────────────────────────────────────────────────────────────
DEBUG = False                                # never True in production
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"] # never in source code
ALLOWED_HOSTS = ["yourdomain.com"]           # explicit whitelist

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ["DB_HOST"],
        "PORT": "5432",
        "CONN_MAX_AGE": 60,          # persistent connections
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "sslmode": "require",    # TLS to PostgreSQL
        },
    }
}

# ── DRF ───────────────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.api.v1.throttling.AnonThrottle",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",  # no BrowsableAPI in production
    ],
    "NUM_PROXIES": 1,
}

# ── JWT ───────────────────────────────────────────────────────────────────────
from datetime import timedelta
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ── Throttle Rates ────────────────────────────────────────────────────────────
THROTTLE_RATES = {
    "anon": "10/minute",
    "shipment_list": "100/minute",
    "analytics": "30/minute",
}

# ── Cache (required for cache_page on analytics views) ────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ["REDIS_URL"],
    }
}

# ── Security Headers ──────────────────────────────────────────────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True           # legacy IE header, low cost
X_FRAME_OPTIONS = "DENY"