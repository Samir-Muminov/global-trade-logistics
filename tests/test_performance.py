"""
tests/test_performance.py

Phase 7: Query count regression tests.
Every test uses CaptureQueriesContext with hard limits — not estimates.

If a test fails with "X queries, expected <= Y", an N+1 was introduced.
These tests are the regression safety net for ORM changes.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.logistics.models import Shipment, ShipmentStatus
from tests.factories import (
    CargoFactory,
    CarrierFactory,
    RouteFactory,
    ShipmentFactory,
    TrackingEventFactory,
)


@pytest.mark.django_db
class TestShipmentListQueryCount:

    def test_list_view_max_3_queries(self, api_client, shipper_user):
        """
        GET /api/v1/shipments/ must issue at most 3 queries regardless
        of how many shipments are returned.

        Expected queries:
          1. Auth token validation (user lookup)
          2. Shipment list with carrier + route JOINs (select_related)
          3. Cursor pagination count (CursorPagination)

        If this exceeds 3, an N+1 was introduced — carrier or route
        is being lazy-loaded per row instead of via select_related.
        """
        carrier = CarrierFactory()
        for _ in range(10):
            ShipmentFactory(
                carrier=carrier,
                status=ShipmentStatus.IN_TRANSIT,
            )

        api_client.force_authenticate(user=shipper_user)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get("/api/v1/shipments/")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        assert len(ctx.captured_queries) <= 3, (
            f"Shipment list issued {len(ctx.captured_queries)} queries — "
            f"expected <= 3. N+1 detected.\n"
            f"Queries:\n" + "\n".join(
                f"  [{i+1}] {q['sql'][:120]}..."
                for i, q in enumerate(ctx.captured_queries)
            )
        )

    def test_list_with_10_shipments_same_count_as_1(self, api_client, shipper_user):
        """
        Query count must not grow with result set size.
        10 shipments must issue the same number of queries as 1 shipment.
        This is the definition of N+1 absence.
        """
        carrier = CarrierFactory()

        ShipmentFactory(carrier=carrier, status=ShipmentStatus.IN_TRANSIT)
        api_client.force_authenticate(user=shipper_user)

        with CaptureQueriesContext(connection) as ctx_one:
            api_client.get("/api/v1/shipments/")
        queries_one = len(ctx_one.captured_queries)

        for _ in range(9):
            ShipmentFactory(carrier=carrier, status=ShipmentStatus.IN_TRANSIT)

        with CaptureQueriesContext(connection) as ctx_ten:
            api_client.get("/api/v1/shipments/")
        queries_ten = len(ctx_ten.captured_queries)

        assert queries_one == queries_ten, (
            f"Query count grew from {queries_one} (1 shipment) "
            f"to {queries_ten} (10 shipments). N+1 detected."
        )


@pytest.mark.django_db
class TestShipmentDetailQueryCount:

    def test_detail_view_max_5_queries(self, api_client, shipper_user):
        """
        GET /api/v1/shipments/{id}/ must issue at most 5 queries.

        Expected queries:
          1. Auth token validation
          2. Shipment with carrier + route JOINs + latest_event subquery
          3. prefetch_related for cargo_items
          4. Permission check (shipper_id lookup)
          5. (spare for any middleware overhead)

        If cargo items are lazy-loaded per item, this will explode.
        prefetch_related("cargo_items") must be in get_queryset().
        """
        shipment = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.IN_TRANSIT,
        )
        for _ in range(5):
            CargoFactory(shipment=shipment)
        TrackingEventFactory(shipment=shipment)

        api_client.force_authenticate(user=shipper_user)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get(f"/api/v1/shipments/{shipment.id}/")

        assert response.status_code == 200
        assert len(ctx.captured_queries) <= 5, (
            f"Shipment detail issued {len(ctx.captured_queries)} queries — "
            f"expected <= 5.\n"
            f"Queries:\n" + "\n".join(
                f"  [{i+1}] {q['sql'][:120]}..."
                for i, q in enumerate(ctx.captured_queries)
            )
        )

    def test_detail_cargo_count_does_not_affect_query_count(
        self, api_client, shipper_user
    ):
        """
        A shipment with 10 cargo items must issue the same number of queries
        as one with 1 cargo item — prefetch_related must be in effect.
        """
        shipment_one = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.IN_TRANSIT,
        )
        CargoFactory(shipment=shipment_one)

        api_client.force_authenticate(user=shipper_user)
        with CaptureQueriesContext(connection) as ctx_one:
            api_client.get(f"/api/v1/shipments/{shipment_one.id}/")
        queries_one = len(ctx_one.captured_queries)

        shipment_ten = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.IN_TRANSIT,
        )
        for _ in range(10):
            CargoFactory(shipment=shipment_ten)

        with CaptureQueriesContext(connection) as ctx_ten:
            api_client.get(f"/api/v1/shipments/{shipment_ten.id}/")
        queries_ten = len(ctx_ten.captured_queries)

        assert queries_one == queries_ten, (
            f"Query count for detail view grew with cargo count: "
            f"{queries_one} (1 cargo) vs {queries_ten} (10 cargo). "
            f"prefetch_related not working."
        )


@pytest.mark.django_db
class TestAnalyticsQueryCount:

    def test_dashboard_summary_exactly_1_query(self, api_client, staff_user):
        """
        GET /api/v1/analytics/dashboard/ must use exactly 1 aggregation query.
        dashboard_summary() uses .aggregate() — single DB round-trip.
        If this grows, the manager method was changed to use multiple queries.
        """
        for _ in range(5):
            ShipmentFactory()

        api_client.force_authenticate(user=staff_user)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get("/api/v1/analytics/dashboard/")

        assert response.status_code == 200
        assert len(ctx.captured_queries) <= 2, (
            f"Dashboard summary issued {len(ctx.captured_queries)} queries — "
            f"expected <= 2 (auth + aggregate)."
        )

    def test_carrier_leaderboard_max_2_queries(self, api_client, shipper_user):
        """
        GET /api/v1/analytics/carriers/leaderboard/ must use at most 2 queries.
        carrier_leaderboard() calls carrier_performance_summary() which is
        a single GROUP BY query. Python-level ranking adds no DB queries.
        """
        carrier = CarrierFactory()
        for _ in range(3):
            ShipmentFactory(carrier=carrier)

        api_client.force_authenticate(user=shipper_user)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get("/api/v1/analytics/carriers/leaderboard/")

        assert response.status_code == 200
        assert len(ctx.captured_queries) <= 2, (
            f"Carrier leaderboard issued {len(ctx.captured_queries)} queries — "
            f"expected <= 2."
        )


@pytest.mark.django_db
class TestQuerySetMethods:

    def test_with_cargo_summary_single_query(self):
        """
        .with_cargo_summary() must issue exactly 1 query (GROUP BY JOIN),
        not 1 query per shipment to fetch cargo.
        """
        for _ in range(5):
            shipment = ShipmentFactory()
            CargoFactory(shipment=shipment)
            CargoFactory(shipment=shipment)

        with CaptureQueriesContext(connection) as ctx:
            results = list(
                Shipment.objects.with_cargo_summary()
            )

        assert len(ctx.captured_queries) == 1, (
            f"with_cargo_summary() issued {len(ctx.captured_queries)} queries — "
            f"expected 1. Aggregation is not being pushed to DB."
        )
        for s in results:
            assert hasattr(s, "total_gross_weight_kg")
            assert hasattr(s, "total_packages")

    def test_with_latest_event_single_query(self):
        """
        .with_latest_event() must issue exactly 1 query using a correlated
        Subquery, not 1 query per shipment (which prefetch_related would cause).
        """
        for _ in range(5):
            shipment = ShipmentFactory()
            TrackingEventFactory(shipment=shipment)
            TrackingEventFactory(shipment=shipment)

        with CaptureQueriesContext(connection) as ctx:
            results = list(Shipment.objects.with_latest_event())

        assert len(ctx.captured_queries) == 1, (
            f"with_latest_event() issued {len(ctx.captured_queries)} queries — "
            f"expected 1 (correlated subquery, not prefetch)."
        )