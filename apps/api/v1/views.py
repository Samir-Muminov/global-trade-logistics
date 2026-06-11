"""
apps/api/v1/views.py
 
Global Trade & Logistics Analytics Platform — Phase 3: API Layer
 
Class-based views (not ViewSets) by design:
ViewSets auto-generate routing via routers, which makes the URL surface
implicit and harder to audit. Explicit CBVs + explicit urls.py means every
endpoint is a named, reviewable line — critical for security audits and
compliance documentation. The tradeoff is more boilerplate; the gain is
full auditability.
 
Authentication: JWT on all endpoints via DEFAULT_AUTHENTICATION_CLASSES.
"""
 
from __future__ import annotations
 
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
 
from apps.api.v1.filters import ShipmentFilter
from apps.api.v1.permissions import IsCarrierActive, IsShipmentOwnerOrStaff, IsStaffOnly
from apps.api.v1.serializers import (
    AnalyticsSummarySerializer,
    CarrierLeaderboardSerializer,
    MonthlyVolumeTrendSerializer,
    ShipmentDetailSerializer,
    ShipmentListSerializer,
)
from apps.api.v1.throttling import AnalyticsThrottle, ShipmentListThrottle
from apps.logistics.models import Shipment
 
 
# ── PAGINATION ─────────────────────────────────────────────────────────────────
 
 
class ShipmentCursorPagination(CursorPagination):
    """
    Cursor-based pagination for the shipment list.
 
    Cursor pagination is used instead of PageNumberPagination for two reasons:
    1. At 10M+ rows, OFFSET-based pagination degrades to O(n) — PostgreSQL must
       scan and discard all preceding rows to reach page N. A cursor at page 100
       with page_size=50 means OFFSET 4950 — full index scan of 4950 rows discarded.
    2. Cursor pagination is stable: new rows inserted between requests do not
       shift page boundaries, so consumers never see duplicate or skipped rows.
 
    Ordered by departure_date descending (most recent first).
    page_size=50 is a balance between payload size and round-trip count.
    """
 
    page_size = 50
    ordering = "-departure_date"
    page_size_query_param = "page_size"
    max_page_size = 200
 
 
# ── SHIPMENT VIEWS ─────────────────────────────────────────────────────────────
 
 
class ShipmentListView(ListAPIView):
    """
    GET /api/v1/shipments/
 
    Returns paginated list of active shipments visible to the requesting user.
    Authentication: JWT required.
    Throttle: ShipmentListThrottle (100/minute).
    Ordering: departure_date only (whitelisted — open ordering would allow
              ORDER BY on unindexed columns, causing full table sorts).
 
    ORM chain:
      .active()           — partial index hit (status IN active set)
      .with_carrier()     — select_related, eliminates N+1 on carrier_name
      .with_route()       — select_related, eliminates N+1 on port names
      .with_delay_flag()  — CASE/WHEN annotation, required by ShipmentFilter.is_delayed
    """
 
    serializer_class = ShipmentListSerializer
    permission_classes = [IsAuthenticated, IsCarrierActive]
    throttle_classes = [ShipmentListThrottle]
    pagination_class = ShipmentCursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = ShipmentFilter
 
    def get_queryset(self):
        return (
            Shipment.objects.active()
            .with_carrier()
            .with_route()
            .with_delay_flag()
            .order_by("-departure_date")
        )
 
 
class ShipmentDetailView(RetrieveAPIView):
    """
    GET /api/v1/shipments/{id}/
 
    Returns full shipment detail for a single shipment.
    Authentication: JWT required.
    Permission: IsShipmentOwnerOrStaff — object-level ownership check.
 
    ORM chain:
      .with_carrier()       — carrier + company in one JOIN
      .with_route()         — route + ports in one JOIN
      .with_latest_event()  — Subquery for latest event_time (no prefetch)
      .with_transit_duration() — ExpressionWrapper for INTERVAL computation
    Prefetch: cargo_items loaded separately via prefetch_related to avoid
              multiplying rows in the JOIN (cargo is one-to-many).
    """
 
    serializer_class = ShipmentDetailSerializer
    permission_classes = [IsAuthenticated, IsShipmentOwnerOrStaff]
    lookup_field = "id"
 
    def get_queryset(self):
        return (
            Shipment.objects
            .with_carrier()
            .with_route()
            .with_latest_event()
            .with_transit_duration()
            .prefetch_related("cargo_items")
        )
 
 
# ── ANALYTICS VIEWS ────────────────────────────────────────────────────────────
 
 
@method_decorator(cache_page(60 * 15), name="dispatch")
class CarrierLeaderboardView(APIView):
    """
    GET /api/v1/analytics/carriers/leaderboard/
 
    Returns carrier performance ranking. Not paginated — rationale:
    The total number of active carriers is bounded (~500 max). Pagination
    would complicate client-side ranking display and add no meaningful
    performance benefit at this cardinality. The full result set is
    < 50KB in JSON.
 
    Cache TTL: 15 minutes. Leaderboard data is a lagging indicator —
    it reflects historical performance, not real-time state. 15-minute
    staleness is acceptable and eliminates repeated full-table aggregation
    on a table with 10M+ rows.
 
    Authentication: JWT required.
    Throttle: AnalyticsThrottle (30/minute — cache misses only).
    """
 
    permission_classes = [IsAuthenticated]
    throttle_classes = [AnalyticsThrottle]
 
    def get(self, request, *args, **kwargs):
        data = Shipment.analytics.carrier_leaderboard()
        serializer = CarrierLeaderboardSerializer(data, many=True)
        return Response(serializer.data)
 
 
@method_decorator(cache_page(60 * 5), name="dispatch")
class DashboardSummaryView(APIView):
    """
    GET /api/v1/analytics/dashboard/
 
    Returns single-query aggregate summary of the full shipments table.
    Staff-only: exposes total declared value across all shipments — not
    appropriate for carrier or shipper users who should only see their own data.
 
    Cache TTL: 5 minutes. Dashboard is refreshed more frequently than the
    leaderboard because ops teams monitor it during active trading hours.
 
    Authentication: JWT + IsStaffOnly.
    Throttle: AnalyticsThrottle (30/minute).
    """
 
    permission_classes = [IsStaffOnly]
    throttle_classes = [AnalyticsThrottle]
 
    def get(self, request, *args, **kwargs):
        summary = Shipment.analytics.dashboard_summary()
        serializer = AnalyticsSummarySerializer(summary)
        return Response(serializer.data)
 
 
class ShipmentRouteAnalyticsView(APIView):
    """
    GET /api/v1/analytics/shipments/trends/?months=N
 
    Returns monthly shipment volume trend for the last N months.
    Default: 12 months. Maximum: 24 months.
 
    Without server-side validation of `months`:
    - A client could send ?months=99999, forcing a GROUP BY TruncMonth scan
      across the entire shipments table history — effectively a DoS via
      expensive aggregation query.
    - Validation caps the window and prevents unbounded historical scans.
 
    Authentication: JWT required.
    Throttle: AnalyticsThrottle (30/minute).
    """
 
    permission_classes = [IsAuthenticated]
    throttle_classes = [AnalyticsThrottle]
 
    def get(self, request, *args, **kwargs):
        try:
            months = int(request.query_params.get("months", 12))
        except (ValueError, TypeError):
            raise ValidationError({"months": "Must be an integer."})
 
        if months < 1 or months > 24:
            raise ValidationError({"months": "Must be between 1 and 24."})
 
        from django.utils import timezone
        cutoff = timezone.now() - timezone.timedelta(days=months * 30)
 
        trend = (
            Shipment.objects
            .filter(departure_date__gte=cutoff)
            .monthly_volume_trend()
        )
        serializer = MonthlyVolumeTrendSerializer(trend, many=True)
        return Response(serializer.data)