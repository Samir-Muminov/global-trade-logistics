"""
config/urls.py

Root URL configuration.

📁 FILE: config/urls.py — REPLACE entire file with this content.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # ── API v1 ────────────────────────────────────────────────────────────────
    path("api/v1/", include("apps.api.v1.urls", namespace="api_v1")),
    path("api/v1/", include("apps.users.urls", namespace="users")),

    # ── OpenAPI schema + Swagger UI ──────────────────────────────────────────
    # /api/schema/  — raw OpenAPI 3.0 JSON
    # /api/docs/    — interactive Swagger UI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]