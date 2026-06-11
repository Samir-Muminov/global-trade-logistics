"""
config/settings/production.py

Production settings for Railway / Vercel deployment.
Extends base.py — only overrides what production requires.

Every setting has an inline comment:
  - what it does
  - what breaks if it is missing or wrong
"""

from __future__ import annotations

import environ

from config.settings.base import *  # noqa: F401, F403

# ── Environment ───────────────────────────────────────────────────────────────
env = environ.Env()

# ── Core Security ─────────────────────────────────────────────────────────────

# DEBUG must be False in production.
# If True: Django serves detailed error pages with stack traces, local vars,
# settings values — full internal exposure to any visitor who triggers a 500.
DEBUG = False

# SECRET_KEY from environment — never hardcoded.
# If missing: Django refuses to start (ImproperlyConfigured).
# If leaked: session cookies, CSRF tokens, and signed data are compromised.
SECRET_KEY = env("DJANGO_SECRET_KEY")

# ALLOWED_HOSTS whitelist — comma-separated in env var.
# If missing or wrong: Django returns 400 Bad Request for every request.
# Example env value: "yourdomain.railway.app,yourdomain.com"
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# ── Database ──────────────────────────────────────────────────────────────────

# DATABASE_URL parsed by django-environ into DATABASES dict.
# Format: postgresql://user:password@host:5432/dbname
# If missing: ImproperlyConfigured at startup.
# If wrong credentials: OperationalError on first DB query.
DATABASES = {
    "default": {
        **env.db("DATABASE_URL"),
        # Reuse DB connections for 60 seconds per worker thread.
        # Without this: new TCP connection opened per HTTP request.
        # At 200 req/s this exhausts PostgreSQL's max_connections (default 100).
        "CONN_MAX_AGE": 60,
        # Verify connection is alive before reusing — prevents stale connection errors
        # after PostgreSQL restarts or network interruptions.
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            # Require TLS to PostgreSQL — plaintext DB connections are unacceptable
            # in production. Railway and Neon enforce this by default.
            "sslmode": "require",
        },
    }
}

# ── Static Files ──────────────────────────────────────────────────────────────

# Where collectstatic writes files. Must exist before build.
# If missing: collectstatic raises ImproperlyConfigured.
STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405

# WhiteNoise serves static files directly from Django process.
# CompressedManifest: gzip compression + content-hash fingerprinting for cache busting.
# If missing: static files return 404 on production (no separate static server).
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

INSTALLED_APPS = [  # noqa: F405
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "apps.logistics",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise must be immediately after SecurityMiddleware.
    # If misplaced: static files bypass compression and caching.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ── TLS / Transport Security ──────────────────────────────────────────────────

# Redirect all HTTP requests to HTTPS at the Django layer.
# If missing: API is accessible over plaintext HTTP — JWT tokens exposed in transit.
SECURE_SSL_REDIRECT = True

# Tell browsers to only access this domain over HTTPS for 1 year.
# If missing: browsers allow HTTP fallback — MITM attack surface remains.
SECURE_HSTS_SECONDS = 31536000  # 1 year

# Apply HSTS to all subdomains.
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Allow inclusion in browser HSTS preload lists.
SECURE_HSTS_PRELOAD = True

# Prevent MIME-type sniffing attacks.
SECURE_CONTENT_TYPE_NOSNIFF = True

# ── Cookies ───────────────────────────────────────────────────────────────────

# Session cookie not accessible via JavaScript.
# If missing: XSS attack can steal session cookie.
SESSION_COOKIE_HTTPONLY = True

# Session cookie only sent over HTTPS.
# If missing: session exposed over HTTP connection.
SESSION_COOKIE_SECURE = True

# SameSite=Lax: cookie sent on same-site requests + top-level navigations.
# Prevents CSRF while allowing normal OAuth redirects.
SESSION_COOKIE_SAMESITE = "Lax"

# CSRF cookie over HTTPS only.
CSRF_COOKIE_SECURE = True

# Prevent clickjacking — no embedding in iframes.
X_FRAME_OPTIONS = "DENY"

# ── DRF ───────────────────────────────────────────────────────────────────────

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        # Global default: every endpoint requires authentication.
        # Individual views override this with tighter permissions.
        # If removed: all endpoints become public — catastrophic.
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        # No BrowsableAPI in production — removes interactive HTML interface
        # that leaks endpoint structure and allows unauthenticated exploration.
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.api.v1.throttling.AnonThrottle",
    ],
    "NUM_PROXIES": 1,  # Trust only last IP in X-Forwarded-For (Railway's load balancer)
}

# ── JWT ───────────────────────────────────────────────────────────────────────

from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    # Short-lived access tokens: 15 min window limits damage from token theft.
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    # Refresh tokens valid for 1 day.
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    # Each refresh issues a new refresh token and blacklists the old one.
    # Without this: stolen refresh token valid for full 1-day window after logout.
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

# ── Caching ───────────────────────────────────────────────────────────────────

REDIS_URL = env("REDIS_URL", default=None)

if REDIS_URL:
    # Redis cache: required for cache_page() on analytics views to work
    # across multiple Railway worker instances.
    # Without Redis: each worker has its own LocMemCache — cache_page is useless
    # in a multi-process deployment (each process caches independently).
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    # Fallback: in-memory cache per process.
    # Acceptable for single-worker deploys or if analytics caching is not critical.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

# ── Logging ───────────────────────────────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": '{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",  # only warnings and above in production logs
    },
    "loggers": {
        "django.security": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}