"""
tests/test_tasks.py

Phase 8: Celery task tests using CELERY_TASK_ALWAYS_EAGER.

CELERY_TASK_ALWAYS_EAGER = True makes tasks execute synchronously
in the same process — no broker needed, no worker needed.
This lets us test task logic without a running Redis/Celery setup.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.logistics.models import ShipmentStatus, TrackingEvent, TrackingEventType
from tests.factories import CarrierFactory, ShipmentFactory, TrackingEventFactory


@pytest.fixture(autouse=True)
def celery_eager(settings):
    """
    Force all Celery tasks to run synchronously in tests.
    Without this, tasks would be sent to a broker that doesn't exist in CI.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.mark.django_db
class TestRefreshMaterializedViewsTask:

    def test_task_runs_without_error(self):
        """
        refresh_materialized_views_task must complete without raising.
        Materialized views exist from migration 0002 — CONCURRENTLY refresh
        on empty views should succeed.
        """
        from apps.logistics.tasks import refresh_materialized_views_task

        result = refresh_materialized_views_task.delay("carrier_daily")
        assert result.successful()
        data = result.get()
        assert data["status"] == "ok"
        assert len(data["refreshed"]) == 1
        assert data["refreshed"][0]["view"] == "mv_carrier_daily_stats"

    def test_task_rejects_unknown_view(self):
        """
        Passing an unknown view name must raise ValueError, not silently succeed.
        This prevents typos in beat schedule from going undetected.
        """
        from apps.logistics.tasks import refresh_materialized_views_task

        with pytest.raises(Exception):
            refresh_materialized_views_task.delay("nonexistent_view").get()

    def test_task_refreshes_all_views(self):
        """ALL refreshes all 3 materialized views in one task call."""
        from apps.logistics.tasks import refresh_materialized_views_task

        result = refresh_materialized_views_task.delay("ALL")
        assert result.successful()
        data = result.get()
        assert len(data["refreshed"]) == 3


@pytest.mark.django_db
class TestPopulateSnapshotsTask:

    def test_task_runs_for_yesterday(self):
        """
        populate_snapshots_task with no date arg defaults to yesterday.
        With no shipments, upserted count is 0 but task succeeds.
        """
        from apps.logistics.tasks import populate_snapshots_task

        result = populate_snapshots_task.delay()
        assert result.successful()
        data = result.get()
        assert data["status"] in ("ok", "partial")
        assert "date" in data
        assert "upserted" in data

    def test_task_is_idempotent(self):
        """
        Running populate_snapshots_task twice for the same date must not
        create duplicate AnalyticsSnapshot rows.
        """
        from apps.logistics.models import AnalyticsSnapshot
        from apps.logistics.tasks import populate_snapshots_task
        import datetime

        yesterday = (timezone.now() - datetime.timedelta(days=1)).date().isoformat()

        ShipmentFactory(
            departure_date=timezone.now() - datetime.timedelta(days=1),
            status=ShipmentStatus.IN_TRANSIT,
        )

        populate_snapshots_task.delay(yesterday, "GLOBAL").get()
        count_after_first = AnalyticsSnapshot.objects.filter(
            granularity="DAILY",
            entity_type="GLOBAL",
        ).count()

        populate_snapshots_task.delay(yesterday, "GLOBAL").get()
        count_after_second = AnalyticsSnapshot.objects.filter(
            granularity="DAILY",
            entity_type="GLOBAL",
        ).count()

        assert count_after_first == count_after_second, (
            f"Idempotency broken: first run={count_after_first}, "
            f"second run={count_after_second}. Duplicate rows created."
        )


@pytest.mark.django_db
class TestProcessUnmatchedWebhookTask:

    def test_task_matches_shipment_on_retry(self):
        """
        If shipment exists at task execution time, TrackingEvent is created.
        Simulates: webhook arrived before our booking, shipment created shortly after.
        """
        from apps.logistics.tasks import process_unmatched_webhook

        carrier = CarrierFactory(carrier_code="TESTSCR", is_active=True)
        shipment = ShipmentFactory(
            carrier=carrier,
            tracking_number="GT-TESTMATCH",
            status=ShipmentStatus.IN_TRANSIT,
        )

        result = process_unmatched_webhook.delay(
            webhook_id="evt_test_match_001",
            carrier_code="TESTSCR",
            shipment_reference="GT-TESTMATCH",
            event_type="vessel.departed",
            payload={"vessel": "MSC OSCAR"},
            received_at=timezone.now().isoformat(),
        )
        assert result.successful()
        data = result.get()
        assert data["status"] == "matched"
        assert data["tracking_number"] == "GT-TESTMATCH"

        assert TrackingEvent.objects.filter(
            shipment=shipment,
            raw_payload__webhook_id="evt_test_match_001",
        ).exists()

    def test_task_is_idempotent_on_duplicate_webhook_id(self):
        """
        Calling the task twice with the same webhook_id must not create
        duplicate TrackingEvents.
        """
        from apps.logistics.tasks import process_unmatched_webhook

        carrier = CarrierFactory(carrier_code="TESTSCR2", is_active=True)
        shipment = ShipmentFactory(
            carrier=carrier,
            tracking_number="GT-TESTDUP",
            status=ShipmentStatus.IN_TRANSIT,
        )

        kwargs = dict(
            webhook_id="evt_test_dup_001",
            carrier_code="TESTSCR2",
            shipment_reference="GT-TESTDUP",
            event_type="vessel.departed",
            payload={},
            received_at=timezone.now().isoformat(),
        )

        process_unmatched_webhook.delay(**kwargs).get()
        process_unmatched_webhook.delay(**kwargs).get()

        count = TrackingEvent.objects.filter(
            raw_payload__webhook_id="evt_test_dup_001"
        ).count()
        assert count == 1, f"Expected 1 TrackingEvent, got {count}. Idempotency broken."

    def test_task_handles_unknown_carrier(self):
        """
        Task with a non-existent carrier_code must return carrier_not_found,
        not raise an unhandled exception that would spam retry queue.
        """
        from apps.logistics.tasks import process_unmatched_webhook

        result = process_unmatched_webhook.delay(
            webhook_id="evt_test_nocarrier",
            carrier_code="NONEXISTENT",
            shipment_reference="GT-WHATEVER",
            event_type="vessel.departed",
            payload={},
            received_at=timezone.now().isoformat(),
        )
        assert result.successful()
        data = result.get()
        assert data["status"] == "carrier_not_found"


@pytest.mark.django_db
class TestSendDelayNotificationsTask:

    def test_creates_delay_event_for_overdue_shipment(self):
        """
        An IN_TRANSIT shipment past ETA by more than threshold_hours
        must get a DELAY TrackingEvent created.
        """
        from apps.logistics.tasks import send_delay_notifications
        import datetime

        overdue_shipment = ShipmentFactory(
            status=ShipmentStatus.IN_TRANSIT,
            estimated_arrival=timezone.now() - datetime.timedelta(hours=5),
            actual_arrival=None,
        )

        result = send_delay_notifications.delay(threshold_hours=2)
        assert result.successful()
        data = result.get()
        assert data["notified"] >= 1

        assert TrackingEvent.objects.filter(
            shipment=overdue_shipment,
            event_type=TrackingEventType.DELAY,
        ).exists()

    def test_does_not_duplicate_delay_events_on_second_run(self):
        """
        Running delay notifications twice in the same day must not create
        two DELAY events for the same shipment.
        Idempotency check: event_time__date=today prevents duplicates.
        """
        from apps.logistics.tasks import send_delay_notifications
        import datetime

        overdue_shipment = ShipmentFactory(
            status=ShipmentStatus.IN_TRANSIT,
            estimated_arrival=timezone.now() - datetime.timedelta(hours=5),
            actual_arrival=None,
        )

        send_delay_notifications.delay(threshold_hours=2).get()
        send_delay_notifications.delay(threshold_hours=2).get()

        count = TrackingEvent.objects.filter(
            shipment=overdue_shipment,
            event_type=TrackingEventType.DELAY,
        ).count()
        assert count == 1, (
            f"Expected 1 DELAY event, got {count}. "
            "Idempotency check on event_time__date failed."
        )

    def test_skips_delivered_shipments(self):
        """
        DELIVERED shipments must never get DELAY events —
        they were delivered, even if late.
        """
        from apps.logistics.tasks import send_delay_notifications
        import datetime

        delivered = ShipmentFactory(
            status=ShipmentStatus.DELIVERED,
            estimated_arrival=timezone.now() - datetime.timedelta(hours=10),
            actual_arrival=timezone.now() - datetime.timedelta(hours=2),
        )

        send_delay_notifications.delay(threshold_hours=2).get()

        assert not TrackingEvent.objects.filter(
            shipment=delivered,
            event_type=TrackingEventType.DELAY,
        ).exists()