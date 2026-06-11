"""
apps/api/v1/filters.py
 
Global Trade & Logistics Analytics Platform — Phase 3: API Layer
Every filter field documents whether it hits an index.
Fields that do not hit an index are marked ⚠️ NO INDEX.
"""
 
from __future__ import annotations
 
from decimal import Decimal
 
import django_filters
from django_filters import rest_framework as filters
 
from apps.logistics.models import Carrier, Shipment, ShipmentStatus
 
 
class ShipmentFilter(filters.FilterSet):
    """
    FilterSet for ShipmentListView.
    All filters are additive (AND logic by default).
    """
 
    # ── Status ────────────────────────────────────────────────────────────────
    # Hits: shipment_carrier_status_idx (composite BTree on carrier_id, status)
    # When combined with carrier filter, full composite index is used.
    status = django_filters.MultipleChoiceFilter(
        choices=ShipmentStatus.choices,
        field_name="status",
        help_text="Filter by one or more statuses. Hits shipment_carrier_status_idx.",
    )
 
    # ── Carrier ───────────────────────────────────────────────────────────────
    # Hits: shipment_carrier_status_idx when combined with status filter.
    # Hits: implicit BTree index on carrier_id FK column standalone.
    carrier = django_filters.ModelChoiceFilter(
        queryset=Carrier.objects.filter(is_active=True),
        field_name="carrier",
        help_text="Filter by carrier UUID. Hits carrier FK index.",
    )
 
    # ── Date Range ────────────────────────────────────────────────────────────
    # Both hit: shipment_departure_date_idx (BTree on departure_date).
    departure_after = django_filters.DateFilter(
        field_name="departure_date",
        lookup_expr="gte",
        help_text="Shipments departing on or after this date. Hits shipment_departure_date_idx.",
    )
    departure_before = django_filters.DateFilter(
        field_name="departure_date",
        lookup_expr="lte",
        help_text="Shipments departing on or before this date. Hits shipment_departure_date_idx.",
    )
 
    # ── Value Range ───────────────────────────────────────────────────────────
    # ⚠️ NO INDEX on declared_value — no BTree index exists on this column.
    # Acceptable only as a secondary filter after a high-selectivity predicate
    # (carrier, status, date range). Never use as the only filter on large datasets.
    min_value = django_filters.NumberFilter(
        field_name="declared_value",
        lookup_expr="gte",
        help_text="⚠️ NO INDEX. Use as secondary filter only.",
    )
    max_value = django_filters.NumberFilter(
        field_name="declared_value",
        lookup_expr="lte",
        help_text="⚠️ NO INDEX. Use as secondary filter only.",
    )
 
    # ── Delay Flag ────────────────────────────────────────────────────────────
    # ⚠️ NO INDEX on is_delayed — this is a computed annotation, not a column.
    # The filter works via CASE/WHEN in the WHERE clause after annotation.
    # Always chain after .with_delay_flag() — ShipmentListView enforces this.
    # Performance impact: requires the delay_flag annotation to be resolved
    # before filtering, which adds a subquery layer. Acceptable at list scale
    # with cursor pagination (never full-table).
    is_delayed = django_filters.BooleanFilter(
        method="filter_is_delayed",
        help_text="⚠️ NO INDEX. Filters on computed annotation. Always paginated.",
    )
 
    def filter_is_delayed(self, queryset, name, value):
        """
        Filter on the is_delayed annotation (0/1 IntegerField).
        Queryset must already have .with_delay_flag() applied — enforced by the view.
        """
        if value is True:
            return queryset.filter(is_delayed=1)
        elif value is False:
            return queryset.filter(is_delayed=0)
        return queryset
 
    class Meta:
        model = Shipment
        fields = [
            "status",
            "carrier",
            "departure_after",
            "departure_before",
            "min_value",
            "max_value",
            "is_delayed",
        ]
 