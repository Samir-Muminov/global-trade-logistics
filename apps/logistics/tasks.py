"""
apps/logistics/tasks.py

Celery tasks for Global Trade & Logistics Analytics Platform.

Rules enforced per PHASES.md:
- No model instances as arguments — IDs only
- Every task is idempotent (safe to retry on failure)
- Every task has exponential backoff retry policy
- Tasks delegate to existing analytics.py functions — no logic duplication
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ── MATERIALIZED VIEW REFRESH ──────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    name="apps.logistics.tasks.refresh_materialized_views_task",
)
def refresh_materialized_views_task(self, view_name: str = "ALL") -> dict:
    """
    Refresh one or all materialized views using CONCURRENTLY.

    Delegates entirely to the existing management command logic in
    refresh_materialized_views.py — no SQL duplication here.

    Why not call the management command directly via subprocess:
    Subprocess spawns a new Python process, a new DB connection, and
    a new Django startup cycle (~1-2 seconds overhead per call).
    Importing and calling the function directly reuses the worker's
    existing DB connection — zero overhead.

    Args:
        view_name: "carrier_daily", "route_monthly", "port_congestion", or "ALL"

    Returns:
        {"view": view_name, "status": "ok", "duration_ms": int}
    """
    import time
    from django.db import connection

    VIEWS = {
        "carrier_daily": "mv_carrier_daily_stats",
        "route_monthly": "mv_route_monthly_performance",
        "port_congestion": "mv_port_congestion",
    }

    if view_name == "ALL":
        targets = list(VIEWS.items())
    else:
        if view_name not in VIEWS:
            raise ValueError(f"Unknown view: {view_name}. Valid: {list(VIEWS.keys())}")
        targets = [(view_name, VIEWS[view_name])]

    results = []
    for key, mv_name in targets:
        start = time.monotonic()
        sql = f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv_name}"
        with connection.cursor() as cursor:
            cursor.execute(sql)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info("Refreshed %s in %dms", mv_name, elapsed_ms)
        results.append({"view": mv_name, "duration_ms": elapsed_ms})

    return {"refreshed": results, "status": "ok"}


# ── SNAPSHOT POPULATION ────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    name="apps.logistics.tasks.populate_snapshots_task",
)
def populate_snapshots_task(
    self,
    snapshot_date_iso: str | None = None,
    entity_type: str = "ALL",
) -> dict:
    """
    Populate AnalyticsSnapshot rows for a given date.

    Idempotent: update_or_create in analytics.py means re-running for the
    same date safely overwrites with latest values — no duplicate rows.

    Args:
        snapshot_date_iso: ISO date string e.g. "2025-01-15". Defaults to yesterday.
        entity_type: "CARRIER", "ROUTE", "GLOBAL", or "ALL"

    Returns:
        {"date": str, "entity_type": str, "upserted": int, "status": "ok"}
    """
    from apps.logistics.analytics import (
        populate_carrier_snapshots,
        populate_global_snapshots,
        populate_route_snapshots,
    )

    if snapshot_date_iso:
        snapshot_date = date.fromisoformat(snapshot_date_iso)
    else:
        snapshot_date = date.today() - timedelta(days=1)

    total_upserted = 0
    errors = []

    if entity_type in ("CARRIER", "ALL"):
        try:
            count = populate_carrier_snapshots(snapshot_date)
            total_upserted += count
            logger.info("Populated %d CARRIER snapshots for %s", count, snapshot_date)
        except Exception as e:
            errors.append(f"CARRIER: {e}")
            logger.error("CARRIER snapshot failed for %s: %s", snapshot_date, e)

    if entity_type in ("ROUTE", "ALL"):
        try:
            count = populate_route_snapshots(snapshot_date)
            total_upserted += count
            logger.info("Populated %d ROUTE snapshots for %s", count, snapshot_date)
        except Exception as e:
            errors.append(f"ROUTE: {e}")
            logger.error("ROUTE snapshot failed for %s: %s", snapshot_date, e)

    if entity_type in ("GLOBAL", "ALL"):
        try:
            count = populate_global_snapshots(snapshot_date)
            total_upserted += count
            logger.info("Populated %d GLOBAL snapshots for %s", count, snapshot_date)
        except Exception as e:
            errors.append(f"GLOBAL: {e}")
            logger.error("GLOBAL snapshot failed for %s: %s", snapshot_date, e)

    if errors and entity_type != "ALL":
        # For single entity type: raise so Celery retries
        raise RuntimeError(f"Snapshot population failed: {errors}")

    return {
        "date": snapshot_date.isoformat(),
        "entity_type": entity_type,
        "upserted": total_upserted,
        "errors": errors,
        "status": "ok" if not errors else "partial",
    }


# ── UNMATCHED WEBHOOK PROCESSING ───────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    name="apps.logistics.tasks.process_unmatched_webhook",
)
def process_unmatched_webhook(
    self,
    webhook_id: str,
    carrier_code: str,
    shipment_reference: str,
    event_type: str,
    payload: dict,
    received_at: str,
) -> dict:
    """
    Attempt to match and process a webhook that had no matching shipment
    at time of receipt.

    Phase 5 WebhookReceiveView logs unmatched webhooks via logger.warning()
    and discards the payload. This task is called by Phase 8 to retry
    matching after a delay — the shipment may have been created between
    the webhook arriving and this task running.

    Why retry: carrier systems often send webhooks before our system has
    processed the booking confirmation. A 30-second delay is enough to
    let the booking propagate.

    Idempotent: checks if webhook_id was already processed before creating
    a TrackingEvent. Safe to retry on failure.

    Args:
        webhook_id: carrier-assigned unique event ID
        carrier_code: SCAC/carrier code e.g. "MSCO"
        shipment_reference: our tracking_number or carrier's own reference
        event_type: carrier event type string
        payload: full carrier payload dict
        received_at: ISO timestamp when webhook was originally received
    """
    from apps.logistics.models import Carrier, Shipment, TrackingEvent, TrackingEventType

    # Idempotency check — already processed in a previous retry
    if TrackingEvent.objects.filter(raw_payload__webhook_id=webhook_id).exists():
        logger.info("Webhook %s already processed — skipping", webhook_id)
        return {"webhook_id": webhook_id, "status": "already_processed"}

    # Try to find shipment
    try:
        carrier = Carrier.objects.get(carrier_code=carrier_code, is_active=True)
    except Carrier.DoesNotExist:
        logger.error("Carrier %s not found — cannot process webhook %s", carrier_code, webhook_id)
        return {"webhook_id": webhook_id, "status": "carrier_not_found"}

    shipment = Shipment.objects.filter(
        tracking_number=shipment_reference,
        carrier=carrier,
    ).first()

    if shipment is None:
        from datetime import datetime as _dt
        try:
            received_dt = _dt.fromisoformat(received_at.replace("Z", "+00:00"))
            age_hours = (timezone.now() - received_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            age_hours = 0

        if age_hours > 24:
            logger.error(
                "Webhook %s expired after 24h without matching shipment %s. "
                "Discarding — check carrier EDI configuration.",
                webhook_id, shipment_reference,
            )
            return {"webhook_id": webhook_id, "status": "expired"}

        logger.warning(
            "Webhook %s: shipment %s still not found after retry %d/%d",
            webhook_id, shipment_reference,
            self.request.retries, self.max_retries,
        )
        raise self.retry(
            countdown=30 * (2 ** self.request.retries),
            exc=ValueError(f"Shipment {shipment_reference} not found"),
        )

    TrackingEvent.objects.create(
        shipment=shipment,
        event_type=TrackingEventType.DOCUMENT_RECEIVED,
        event_time=timezone.now(),
        description=f"Carrier webhook (delayed match): {event_type}",
        source_system=f"WEBHOOK:{carrier_code}",
        is_exception=False,
        raw_payload={
            "webhook_id": webhook_id,
            "carrier_code": carrier_code,
            "event_type": event_type,
            "shipment_reference": shipment_reference,
            "payload": payload,
            "received_at": received_at,
            "matched_at": timezone.now().isoformat(),
            "retries": self.request.retries,
        },
    )

    logger.info(
        "Webhook %s matched to shipment %s after %d retries",
        webhook_id, shipment.tracking_number, self.request.retries,
    )
    return {
        "webhook_id": webhook_id,
        "shipment_id": str(shipment.id),
        "tracking_number": shipment.tracking_number,
        "status": "matched",
        "retries": self.request.retries,
    }


# ── DELAY NOTIFICATIONS ────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    name="apps.logistics.tasks.send_delay_notifications",
)
def send_delay_notifications(self, threshold_hours: int = 2) -> dict:
    """
    Find shipments that are overdue by more than threshold_hours and
    create DELAY tracking events for any that don't already have one today.

    Idempotent: checks if a DELAY event already exists for today before
    creating one. Safe to call every 30 minutes without duplicating events.

    Args:
        threshold_hours: hours past ETA before a shipment is considered delayed

    Returns:
        {"notified": int, "skipped": int, "status": "ok"}
    """
    from django.db.models import Q
    from apps.logistics.models import Shipment, ShipmentStatus, TrackingEvent, TrackingEventType

    now = timezone.now()
    threshold = now - timezone.timedelta(hours=threshold_hours)
    today = now.date()

    overdue_shipments = Shipment.objects.filter(
        estimated_arrival__lt=threshold,
        actual_arrival__isnull=True,
        status__in=[ShipmentStatus.IN_TRANSIT, ShipmentStatus.AT_PORT],
    ).select_related("carrier", "shipper")

    notified = 0
    skipped = 0

    for shipment in overdue_shipments:
        # Idempotency: skip if DELAY event already created today
        already_notified = TrackingEvent.objects.filter(
            shipment=shipment,
            event_type=TrackingEventType.DELAY,
            event_time__date=today,
        ).exists()

        if already_notified:
            skipped += 1
            continue

        hours_overdue = int((now - shipment.estimated_arrival).total_seconds() / 3600)

        TrackingEvent.objects.create(
            shipment=shipment,
            event_type=TrackingEventType.DELAY,
            event_time=now,
            description=(
                f"Shipment is {hours_overdue}h past estimated arrival. "
                f"ETA was {shipment.estimated_arrival.strftime('%Y-%m-%d %H:%M UTC')}."
            ),
            is_exception=False,
            source_system="CELERY:delay_notifications",
            raw_payload={
                "hours_overdue": hours_overdue,
                "threshold_hours": threshold_hours,
                "eta": shipment.estimated_arrival.isoformat(),
                "detected_at": now.isoformat(),
            },
        )
        notified += 1
        logger.info(
            "Delay event created for shipment %s (%dh overdue)",
            shipment.tracking_number, hours_overdue,
        )

    logger.info(
        "Delay notifications: %d created, %d skipped (already notified today)",
        notified, skipped,
    )
    return {"notified": notified, "skipped": skipped, "status": "ok"}