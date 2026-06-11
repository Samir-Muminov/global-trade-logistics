
"""
apps/api/v1/urls.py
 
Global Trade & Logistics Analytics Platform — Phase 3: API Layer
Every URL is explicit and named. No auto-generated router URLs.
Auditable surface: every endpoint is one line in this file.
"""
 
from __future__ import annotations
 
from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
 
from apps.api.v1.views import (
    CarrierLeaderboardView,
    DashboardSummaryView,
    ShipmentDetailView,
    ShipmentListView,
    ShipmentRouteAnalyticsView,
)
 
app_name = "api_v1"
 
urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────────────────
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
 
    # ── Shipments ─────────────────────────────────────────────────────────────
    path("shipments/", ShipmentListView.as_view(), name="shipment_list"),
    path("shipments/<uuid:id>/", ShipmentDetailView.as_view(), name="shipment_detail"),
 
    # ── Analytics ─────────────────────────────────────────────────────────────
    path("analytics/carriers/leaderboard/", CarrierLeaderboardView.as_view(), name="carrier_leaderboard"),
    path("analytics/dashboard/", DashboardSummaryView.as_view(), name="dashboard_summary"),
    path("analytics/shipments/trends/", ShipmentRouteAnalyticsView.as_view(), name="shipment_trends"),
]
 