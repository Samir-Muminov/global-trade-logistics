"""
apps/api/v1/urls.py

Global Trade & Logistics Analytics Platform — Phase 5: Full URL routing
Every endpoint is explicit and named. No auto-generated router URLs.

Read endpoints (Phase 3) + Write endpoints (Phase 5) + Auth (Phase 6, in apps.users.urls).
"""

from __future__ import annotations

from django.urls import path

from apps.api.v1.views import (
    CarrierLeaderboardView,
    DashboardSummaryView,
    ShipmentDetailView,
    ShipmentListView,
    ShipmentRouteAnalyticsView,
)
from apps.api.v1.views_write import (
    CargoCreateView,
    HealthCheckView,
    ShipmentCreateView,
    ShipmentStatusUpdateView,
    TrackingEventCreateView,
    WebhookReceiveView,
)

app_name = "api_v1"

urlpatterns = [
    # ── Health ────────────────────────────────────────────────────────────────
    path("health/", HealthCheckView.as_view(), name="health_check"),

    # ── Shipments — Read (Phase 3) ───────────────────────────────────────────
    path("shipments/", ShipmentListView.as_view(), name="shipment_list"),
    path("shipments/<uuid:id>/", ShipmentDetailView.as_view(), name="shipment_detail"),

    # ── Shipments — Write (Phase 5) ──────────────────────────────────────────
    path("shipments/create/", ShipmentCreateView.as_view(), name="shipment_create"),
    path(
        "shipments/<uuid:id>/status/",
        ShipmentStatusUpdateView.as_view(),
        name="shipment_status_update",
    ),
    path(
        "shipments/<uuid:id>/cargo/",
        CargoCreateView.as_view(),
        name="cargo_create",
    ),
    path(
        "shipments/<uuid:id>/events/",
        TrackingEventCreateView.as_view(),
        name="tracking_event_create",
    ),

    # ── Webhooks (Phase 5) ────────────────────────────────────────────────────
    path(
        "webhooks/carrier/<str:carrier_code>/",
        WebhookReceiveView.as_view(),
        name="carrier_webhook",
    ),

    # ── Analytics (Phase 3) ───────────────────────────────────────────────────
    path("analytics/carriers/leaderboard/", CarrierLeaderboardView.as_view(), name="carrier_leaderboard"),
    path("analytics/dashboard/", DashboardSummaryView.as_view(), name="dashboard_summary"),
    path("analytics/shipments/trends/", ShipmentRouteAnalyticsView.as_view(), name="shipment_trends"),
]