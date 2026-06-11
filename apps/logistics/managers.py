"""
apps/logistics/managers.py

Global Trade & Logistics Analytics Platform — Phase 2: Advanced ORM Query Layer
Managers are thin orchestration layers over QuerySets.
No filtering logic lives here — all logic lives in querysets.py.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.logistics.querysets import (
    CarrierQuerySet,
    PortQuerySet,
    ShipmentQuerySet,
    TrackingEventQuerySet,
)


# ── SHIPMENT MANAGERS ──────────────────────────────────────────────────────────


class ShipmentManager(models.Manager.from_queryset(ShipmentQuerySet)):
    pass


class ShipmentAnalyticsManager(models.Manager.from_queryset(ShipmentQuerySet)):
    """
    Analytics manager for Shipment. Exposes reporting methods.
    Never redefines filtering logic — delegates entirely to ShipmentQuerySet.

    use_in_migrations = False: this manager is not needed by the migration
    framework and should not appear in migration state. Prevents Django from
    trying to serialise it into migration files.

    Attach to model as: analytics = ShipmentAnalyticsManager()
    """

    use_in_migrations = False

    def get_queryset(self) -> ShipmentQuerySet:
        return ShipmentQuerySet(self.model, using=self._db)

    def dashboard_summary(self) -> dict:
        """
        Single-query dict with top-level operational metrics.

        Returns:
            total: total shipment count
            active: in-transit + booked + at-port count
            delayed: count of shipments past ETA
            total_declared_value: sum of declared values (Decimal)

        Uses conditional COUNT aggregation — one DB round-trip, no subqueries.
        Hits: shipment_carrier_status_idx for the status-based counts.
        """
        from django.db.models import Count, Q, Sum
        from django.db.models.functions import Now

        qs = self.get_queryset()
        result = qs.aggregate(
            total=Count("id"),
            active=Count(
                "id",
                filter=Q(status__in=["BOOKED", "IN_TRANSIT", "AT_PORT"]),
            ),
            delayed=Count(
                "id",
                filter=Q(estimated_arrival__lt=Now())
                & ~Q(status__in=["DELIVERED", "CANCELLED"]),
            ),
            total_declared_value=models.Sum(
                "declared_value",
                output_field=models.DecimalField(max_digits=22, decimal_places=2),
            ),
        )
        # Coerce None to Decimal(0) for total_declared_value (all NULLs edge case)
        result["total_declared_value"] = result["total_declared_value"] or Decimal("0")
        return result

    def top_routes(self, limit: int = 10) -> models.QuerySet:
        """
        Route utilisation report scoped to the last 90 days.

        Delegates to ShipmentQuerySet.route_utilization_report() with a
        date range pre-filter. Returns at most `limit` routes by shipment volume.

        Hits: shipment_departure_date_idx for the date filter,
              then route FK index for GROUP BY.
        """
        cutoff = timezone.now() - timezone.timedelta(days=90)
        return (
            self.get_queryset()
            .filter(departure_date__gte=cutoff)
            .route_utilization_report()[:limit]
        )

    def carrier_leaderboard(self) -> models.QuerySet:
        """
        Carrier performance summary with volume rank window annotation.

        Combines carrier_performance_summary() (terminal aggregation) with
        with_carrier_rank_by_volume() context.

        ⚠️ Window functions cannot be applied after .values().annotate()
        in a single queryset chain — the GROUP BY and OVER() clauses conflict.
        Solution: carrier_performance_summary() is the terminal aggregation;
        the rank is computed via Python sorted() on the materialised result,
        which is correct at this cardinality (max ~500 carriers, not 10M rows).

        Returns a list of dicts sorted by shipment_count descending,
        with a `volume_rank` key added in Python.
        """
        rows = list(self.get_queryset().carrier_performance_summary())
        for rank, row in enumerate(rows, start=1):
            row["volume_rank"] = rank
        return rows


# ── CARRIER MANAGERS ───────────────────────────────────────────────────────────


class CarrierManager(models.Manager.from_queryset(CarrierQuerySet)):
      pass


class CarrierAnalyticsManager(models.Manager):
    """
    Analytics manager for Carrier.
    Attach to model as: analytics = CarrierAnalyticsManager()
    """

    use_in_migrations = False

    def get_queryset(self) -> CarrierQuerySet:
        return CarrierQuerySet(self.model, using=self._db)

    def active_by_mode(self, mode: str) -> CarrierQuerySet:
        """
        Returns active carriers for a given mode with shipment counts.
        Hits: carrier_mode_active_idx (composite BTree on mode, is_active).
        """
        return (
            self.get_queryset()
            .active()
            .by_mode(mode)
            .with_company()
            .with_shipment_counts()
            .order_by("-shipment_count")
        )

    def hub_coverage(self, un_locode: str) -> CarrierQuerySet:
        """
        Returns active carriers that hub through a specific port,
        annotated with shipment counts.

        Hits: carrier_hub_ports_gin_idx (GIN array containment).
        """
        return (
            self.get_queryset()
            .active()
            .hubbing_through(un_locode)
            .with_company()
            .with_shipment_counts()
        )


# ── PORT MANAGER ───────────────────────────────────────────────────────────────


class PortManager(models.Manager.from_queryset(PortQuerySet)):
    pass


# ── TRACKING EVENT MANAGER ─────────────────────────────────────────────────────


class TrackingEventManager(models.Manager.from_queryset(TrackingEventQuerySet)):
    pass
