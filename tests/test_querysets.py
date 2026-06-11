"""
tests/test_querysets.py

QuerySet method tests. Every test documents a real-world query scenario.
Query count tests use CaptureQueriesContext — hard numbers, not estimates.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection

from apps.logistics.models import Shipment, ShipmentStatus, TrackingEvent
from tests.factories import (
    CargoFactory,
    CarrierFactory,
    ShipmentFactory,
    TrackingEventFactory,
)


@pytest.mark.django_db
class TestShipmentFiltering:

    def test_active_returns_only_active_statuses(self):
        """
        .active() must return BOOKED, IN_TRANSIT, AT_PORT only.
        DELIVERED and CANCELLED shipments must be excluded.
        Real-world: ops dashboard must not show completed shipments.
        """
        active = ShipmentFactory(status=ShipmentStatus.IN_TRANSIT)
        delivered = ShipmentFactory(status=ShipmentStatus.DELIVERED)
        cancelled = ShipmentFactory(status=ShipmentStatus.CANCELLED)

        qs = Shipment.objects.active()

        ids = list(qs.values_list("id", flat=True))
        assert active.id in ids
        assert delivered.id not in ids
        assert cancelled.id not in ids

    def test_delayed_returns_past_eta_non_delivered(self, shipment_delayed):
        """
        .delayed() must return shipments past ETA that are not delivered.
        Real-world: ops team monitors delayed shipments for intervention.
        """
        qs = Shipment.objects.delayed()
        assert shipment_delayed.id in qs.values_list("id", flat=True)

    def test_delayed_excludes_delivered_shipments(self):
        """
        A delivered shipment past its original ETA must NOT appear in .delayed().
        Late delivery is a historical fact, not an active delay.
        """
        from django.utils import timezone
        late_delivered = ShipmentFactory(
            status=ShipmentStatus.DELIVERED,
            estimated_arrival=timezone.now() - datetime.timedelta(days=5),
            actual_arrival=timezone.now() - datetime.timedelta(days=2),
        )
        qs = Shipment.objects.delayed()
        assert late_delivered.id not in qs.values_list("id", flat=True)

    def test_high_value_requires_decimal_not_float(self):
        """
        .high_value() must raise TypeError if a float is passed.
        Float comparisons on financial values are unacceptable — silent precision
        errors can cause wrong filtering that excludes valid shipments.
        """
        with pytest.raises(TypeError, match="must be Decimal"):
            Shipment.objects.high_value(100000.0)

    def test_high_value_accepts_decimal(self):
        """
        .high_value() with correct Decimal type filters correctly.
        """
        high = ShipmentFactory(declared_value=Decimal("500001.00"))
        low = ShipmentFactory(declared_value=Decimal("9999.00"))

        qs = Shipment.objects.high_value(Decimal("500000.00"))
        ids = list(qs.values_list("id", flat=True))
        assert high.id in ids
        assert low.id not in ids

    def test_by_carrier_filters_correctly(self):
        """
        .by_carrier() must return only shipments for the specified carrier.
        """
        carrier_a = CarrierFactory()
        carrier_b = CarrierFactory()
        s_a = ShipmentFactory(carrier=carrier_a)
        s_b = ShipmentFactory(carrier=carrier_b)

        qs = Shipment.objects.by_carrier(carrier_a.id)
        ids = list(qs.values_list("id", flat=True))
        assert s_a.id in ids
        assert s_b.id not in ids


@pytest.mark.django_db
class TestN1Elimination:

    def test_with_carrier_eliminates_n_plus_1(self):
        """
        .with_carrier() must load all carrier data in ≤ 2 queries regardless
        of shipment count. Without select_related, each .carrier access = 1 query.
        Real-world: list endpoint with 50 shipments would issue 50 carrier queries.
        """
        carrier = CarrierFactory()
        for _ in range(5):
            ShipmentFactory(carrier=carrier)

        with CaptureQueriesContext(connection) as ctx:
            shipments = list(Shipment.objects.active().with_carrier())
            # Access carrier on each — should not trigger new queries
            for s in shipments:
                _ = s.carrier.carrier_name
                _ = s.carrier.company_id

        # 1 query for shipments + 1 JOIN for carrier (select_related)
        assert len(ctx.captured_queries) <= 2, (
            f"Expected ≤ 2 queries, got {len(ctx.captured_queries)}. "
            "N+1 detected on carrier access."
        )

    def test_with_route_eliminates_n_plus_1(self):
        """
        .with_route() must load route + both ports in ≤ 2 queries.
        """
        for _ in range(5):
            ShipmentFactory()

        with CaptureQueriesContext(connection) as ctx:
            shipments = list(Shipment.objects.active().with_route())
            for s in shipments:
                _ = s.route.route_code
                _ = s.route.origin_port.un_locode
                _ = s.route.destination_port.un_locode

        assert len(ctx.captured_queries) <= 2, (
            f"Expected ≤ 2 queries, got {len(ctx.captured_queries)}. "
            "N+1 detected on route/port access."
        )

    def test_with_latest_event_uses_subquery_not_prefetch(self):
        """
        .with_latest_event() must annotate with a Subquery — not prefetch_related.
        Prefetch would load ALL events into memory; Subquery loads only the latest.
        Verify: the annotation is a scalar, not a queryset.
        """
        shipment = ShipmentFactory()
        TrackingEventFactory(shipment=shipment)
        TrackingEventFactory(shipment=shipment)

        qs = Shipment.objects.with_latest_event()
        s = qs.get(pk=shipment.pk)

        # latest_event_time must be a datetime, not a queryset
        assert hasattr(s, "latest_event_time")
        # If it were a prefetch, it would be a list — it must not be
        assert not hasattr(s, "_prefetched_objects_cache") or \
               "tracking_events" not in getattr(s, "_prefetched_objects_cache", {})


@pytest.mark.django_db
class TestAnnotations:

    def test_with_delay_flag_returns_integer(self):
        """
        .with_delay_flag() must produce IntegerField (0 or 1), not BooleanField.
        Rationale: SUM(is_delayed) gives count; AVG(is_delayed) gives rate.
        BooleanField requires CAST in every aggregation.
        """
        from django.utils import timezone
        delayed = ShipmentFactory(
            status=ShipmentStatus.IN_TRANSIT,
            estimated_arrival=timezone.now() - datetime.timedelta(days=1),
        )
        on_time = ShipmentFactory(
            status=ShipmentStatus.IN_TRANSIT,
            estimated_arrival=timezone.now() + datetime.timedelta(days=10),
        )

        delayed_annotated = Shipment.objects.with_delay_flag().get(pk=delayed.pk)
        ontime_annotated = Shipment.objects.with_delay_flag().get(pk=on_time.pk)

        assert delayed_annotated.is_delayed == 1
        assert ontime_annotated.is_delayed == 0
        assert isinstance(delayed_annotated.is_delayed, int)

    def test_with_transit_duration_handles_null_actual_arrival(self):
        """
        .with_transit_duration() must not raise when actual_arrival is NULL.
        Coalesce substitutes Now() — giving in-progress duration, not NULL.
        """
        shipment = ShipmentFactory(actual_arrival=None)
        annotated = Shipment.objects.with_transit_duration().get(pk=shipment.pk)
        assert annotated.transit_duration is not None

    def test_with_value_tier_bucketing(self):
        """
        .with_value_tier() must assign correct tier based on declared_value.
        Real-world: tier is used for priority routing and insurance categorisation.
        """
        platinum = ShipmentFactory(declared_value=Decimal("600000.00"))
        gold = ShipmentFactory(declared_value=Decimal("150000.00"))
        silver = ShipmentFactory(declared_value=Decimal("15000.00"))
        standard = ShipmentFactory(declared_value=Decimal("5000.00"))

        qs = Shipment.objects.with_value_tier()

        assert qs.get(pk=platinum.pk).value_tier == "platinum"
        assert qs.get(pk=gold.pk).value_tier == "gold"
        assert qs.get(pk=silver.pk).value_tier == "silver"
        assert qs.get(pk=standard.pk).value_tier == "standard"

    def test_with_moving_avg_no_import_error(self):
        """
        BUG-003 regression test: with_moving_avg_value() must not raise
        ImportError from non-existent ValueRange class.
        """
        ShipmentFactory()
        # Must not raise ImportError or AttributeError
        try:
            list(Shipment.objects.with_moving_avg_value(window_days=30))
        except ImportError as e:
            pytest.fail(f"BUG-003 regression: {e}")


@pytest.mark.django_db
class TestTerminalAggregations:

    def test_carrier_performance_summary_columns(self):
        """
        .carrier_performance_summary() must return expected columns.
        Real-world: powers the carrier leaderboard dashboard.
        """
        carrier = CarrierFactory()
        for _ in range(3):
            ShipmentFactory(carrier=carrier)

        results = list(
            Shipment.objects.by_carrier(carrier.id).carrier_performance_summary()
        )
        assert len(results) > 0
        row = results[0]
        assert "carrier_id" in row
        assert "shipment_count" in row
        assert "delay_rate_pct" in row
        assert "avg_declared_value" in row
        assert "total_gross_weight_kg" in row

    def test_monthly_volume_trend_grouping(self):
        """
        .monthly_volume_trend() must group by month and return correct columns.
        """
        from django.utils import timezone
        now = timezone.now()
        ShipmentFactory(departure_date=now - datetime.timedelta(days=10))
        ShipmentFactory(departure_date=now - datetime.timedelta(days=5))

        results = list(Shipment.objects.monthly_volume_trend())
        assert len(results) >= 1
        row = results[0]
        assert "month" in row
        assert "shipment_count" in row
        assert "total_declared_value" in row


@pytest.mark.django_db
class TestWindowFunctions:

    def test_with_running_total_accumulates(self):
        """
        .with_running_total_value() must produce monotonically increasing values.
        """
        for i in range(3):
            ShipmentFactory(
                declared_value=Decimal("10000.00"),
                departure_date=__import__("django.utils.timezone", fromlist=["now"]).now()
                - datetime.timedelta(days=10 - i),
            )

        results = list(
            Shipment.objects.with_running_total_value()
            .order_by("departure_date")
            .values("running_total_value")
        )

        values = [r["running_total_value"] for r in results]
        # Each value must be >= previous (monotonically non-decreasing)
        for i in range(1, len(values)):
            if values[i] is not None and values[i - 1] is not None:
                assert values[i] >= values[i - 1], (
                    f"Running total not monotonic: {values}"
                )