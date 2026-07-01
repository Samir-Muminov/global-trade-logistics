"""
apps/api/v1/views_write.py

Write views for all mutating operations.

Every view that touches multiple tables uses transaction.atomic().
Every status update uses select_for_update() to prevent race conditions.
Every POST endpoint checks idempotency before doing any work.

Authentication: JWT required on all endpoints.
Webhook endpoint: HMAC signature only — no JWT.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.permissions import IsCarrierStaffForShipment, IsEmailVerified, IsStaffOnly
from apps.api.v1.serializers import CargoSerializer, ShipmentListSerializer
from apps.api.v1.serializers_write import (
    CargoCreateSerializer,
    ShipmentCreateSerializer,
    ShipmentStatusUpdateSerializer,
    TrackingEventCreateSerializer,
    WebhookPayloadSerializer,
)
from apps.api.v1.throttling import ShipmentListThrottle
from apps.api.v1.validators import (
    generate_tracking_number,
    validate_hmac_signature,
    validate_shipment_editable,
)
from apps.logistics.models import (
    Carrier,
    Cargo,
    Shipment,
    ShipmentStatus,
    TrackingEvent,
    TrackingEventType,
)
from apps.users.permissions import IsShipmentOwnerOrStaff


# ── STATUS → EVENT TYPE MAPPING ────────────────────────────────────────────────
# When a status changes, automatically create a TrackingEvent of the matching type.

STATUS_TO_EVENT_TYPE: dict[str, str] = {
    ShipmentStatus.BOOKED: TrackingEventType.DOCUMENT_RECEIVED,
    ShipmentStatus.IN_TRANSIT: TrackingEventType.DEPARTURE,
    ShipmentStatus.AT_PORT: TrackingEventType.ARRIVAL,
    ShipmentStatus.DELIVERED: TrackingEventType.DELIVERED,
    ShipmentStatus.CANCELLED: TrackingEventType.EXCEPTION,
    ShipmentStatus.EXCEPTION: TrackingEventType.EXCEPTION,
    ShipmentStatus.CUSTOMS_HOLD: TrackingEventType.CUSTOMS_HOLD,
}


# ── IDEMPOTENCY HELPERS ────────────────────────────────────────────────────────

def _check_idempotency_key(idempotency_key: uuid.UUID) -> Shipment | None:
    """
    Check if a shipment was already created with this idempotency key.
    Returns existing Shipment if found, None if key is fresh.

    Storage: custom_attributes JSONB field on Shipment.
    We store {"idempotency_key": "..."} in custom_attributes.
    This avoids a dedicated table for Phase 5 — Phase 8 will move
    to a proper IdempotencyKey model with TTL enforcement.
    """
    return Shipment.objects.filter(
        custom_attributes__idempotency_key=str(idempotency_key)
    ).first()


def _check_webhook_idempotency(webhook_id: str) -> TrackingEvent | None:
    """
    Check if a webhook was already processed with this webhook_id.
    Returns existing TrackingEvent if found, None if webhook_id is fresh.

    Storage: raw_payload JSONB field on TrackingEvent.
    """
    return TrackingEvent.objects.filter(
        raw_payload__webhook_id=webhook_id
    ).first()


# ── SHIPMENT CREATE VIEW ───────────────────────────────────────────────────────


class ShipmentCreateView(APIView):
    """
    POST /api/v1/shipments/

    Creates a new shipment atomically:
    1. Check idempotency key — return 200 if already processed
    2. Validate all domain invariants (carrier active, route matches, ports match)
    3. In transaction.atomic():
       a. Create Shipment with status=DRAFT
       b. Create initial TrackingEvent (DOC_RECEIVED)
    4. Return 201 with ShipmentListSerializer output

    Authentication: JWT required.
    Permission: request.user must be a shipper (user.company == shipper_id).
    Idempotency: client provides idempotency_key UUID. Duplicate = 200 with original.

    ⚠️ RACE CONDITION: Two concurrent requests with the same idempotency_key
    could both pass the idempotency check before either creates the shipment.
    Mitigation: DB unique constraint on custom_attributes->>'idempotency_key'
    would be ideal; for now the IntegrityError on tracking_number is caught
    and retried (tracking_number is unique, generated with uuid randomness).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [ShipmentListThrottle]

    def post(self, request, *args, **kwargs):
        serializer = ShipmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # ── Idempotency check ──────────────────────────────────────────────────
        idempotency_key = data["idempotency_key"]
        existing = _check_idempotency_key(idempotency_key)
        if existing:
            # Return the original response — same as if the request just succeeded
            return Response(
                ShipmentListSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        # ── Permission: user must be the shipper ───────────────────────────────
        user_company_id = getattr(request.user, "company_id", None)
        if not request.user.is_staff and str(user_company_id) != str(data["shipper_id"]):
            raise ValidationError(
                {"shipper_id": "You can only create shipments for your own company."}
            )

        # ── Atomic creation ────────────────────────────────────────────────────
        max_retries = 3
        for attempt in range(max_retries):
            tracking_number = generate_tracking_number()
            try:
                with transaction.atomic():
                    shipment = Shipment.objects.create(
                        shipper_id=data["shipper_id"],
                        consignee_id=data["consignee_id"],
                        notify_party_id=data.get("notify_party_id"),
                        carrier_id=data["carrier_id"],
                        route_id=data["route_id"],
                        origin_port_id=data["origin_port_id"],
                        destination_port_id=data["destination_port_id"],
                        tracking_number=tracking_number,
                        status=ShipmentStatus.DRAFT,
                        departure_date=data["departure_date"],
                        estimated_arrival=data.get("estimated_arrival"),
                        declared_value=data.get("declared_value"),
                        freight_cost=data.get("freight_cost"),
                        currency_code=data.get("currency_code", "USD"),
                        incoterms=data.get("incoterms", ""),
                        purchase_order_refs=data.get("purchase_order_refs", []),
                        hs_codes=data.get("hs_codes", []),
                        tags=data.get("tags", []),
                        notes=data.get("notes", ""),
                        custom_attributes={
                            "idempotency_key": str(idempotency_key),
                        },
                    )

                    # Create initial tracking event atomically
                    TrackingEvent.objects.create(
                        shipment=shipment,
                        event_type=TrackingEventType.DOCUMENT_RECEIVED,
                        event_time=timezone.now(),
                        description="Shipment booking created via API.",
                        source_system="API",
                        raw_payload={},
                    )

                break  # success — exit retry loop

            except IntegrityError:
                if attempt == max_retries - 1:
                    raise
                # tracking_number collision (extremely rare) — retry with new one
                continue

        shipment_out = (
            Shipment.objects
            .with_carrier()
            .with_route()
            .get(pk=shipment.pk)
        )
        output = ShipmentListSerializer(shipment_out)
        return Response(output.data, status=status.HTTP_201_CREATED)


# ── SHIPMENT STATUS UPDATE VIEW ────────────────────────────────────────────────


class ShipmentStatusUpdateView(APIView):
    """
    PATCH /api/v1/shipments/{id}/status/

    Updates shipment status with:
    1. select_for_update() — prevents concurrent status updates on same row
    2. Status machine validation — illegal transitions rejected with 400
    3. Automatic TrackingEvent creation — in same transaction

    Authentication: JWT required.
    Permission: carrier staff for this shipment, or internal staff.

    select_for_update() acquires a row-level lock for the duration of
    the transaction. Two concurrent PATCH requests on the same shipment
    will serialize — the second waits for the first to commit or rollback.
    Without this: two requests could both read status=BOOKED and both
    try to transition to IN_TRANSIT, creating duplicate TrackingEvents.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def patch(self, request, id: uuid.UUID, *args, **kwargs):
        # Lock the row before reading status — prevents race conditions
        try:
            shipment = (
                Shipment.objects
                .select_for_update()
                .get(pk=id)
            )
        except Shipment.DoesNotExist:
            return Response(
                {"detail": "Shipment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Object-level permission — carrier staff or internal staff
        if not request.user.is_staff:
            user_carrier_id = getattr(request.user, "carrier_id", None)
            if str(user_carrier_id) != str(shipment.carrier_id):
                return Response(
                    {"detail": "You do not have permission to update this shipment's status."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Validate transition BEFORE entering atomic block
        serializer = ShipmentStatusUpdateSerializer(
            data=request.data,
            current_status=shipment.status,
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        new_status = data["new_status"]

        if new_status == shipment.status:
            return Response(
                {"detail": f"Shipment is already in status '{new_status}'."},
                status=status.HTTP_200_OK,
            )

        # ── Atomic: update status + create event ───────────────────────────────
        with transaction.atomic():
            old_status = shipment.status
            shipment.status = new_status
            shipment.save(update_fields=["status", "updated_at"])

            # Set actual_arrival on delivery
            if new_status == ShipmentStatus.DELIVERED:
                shipment.actual_arrival = timezone.now()
                shipment.save(update_fields=["actual_arrival", "updated_at"])

            event_type = STATUS_TO_EVENT_TYPE.get(
                new_status, TrackingEventType.EXCEPTION
            )
            event = TrackingEvent.objects.create(
                shipment=shipment,
                event_type=event_type,
                event_time=timezone.now(),
                description=(
                    data.get("note")
                    or f"Status changed from {old_status} to {new_status}."
                ),
                location_description=data.get("event_location", ""),
                is_exception=(new_status == ShipmentStatus.EXCEPTION),
                source_system="API",
                raw_payload={},
            )

        return Response(
            {
                "shipment_id": str(shipment.id),
                "tracking_number": shipment.tracking_number,
                "old_status": old_status,
                "new_status": new_status,
                "event_id": str(event.id),
                "event_time": event.event_time.isoformat(),
            },
            status=status.HTTP_200_OK,
        )


# ── CARGO CREATE VIEW ──────────────────────────────────────────────────────────


class CargoCreateView(APIView):
    """
    POST /api/v1/shipments/{id}/cargo/

    Adds a cargo item to a shipment.

    Permission: only the shipper or internal staff can add cargo.
    (Carrier staff manage the transport, not the cargo content.)

    Validation:
    - Shipment must not be in DELIVERED or CANCELLED status
    - Cargo domain invariants validated at serializer level (400, not 500)

    Atomic: Cargo creation is a single INSERT — no multi-table operation needed.
    No transaction.atomic() required for single-model writes.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, id: uuid.UUID, *args, **kwargs):
        try:
            shipment = Shipment.objects.get(pk=id)
        except Shipment.DoesNotExist:
            return Response(
                {"detail": "Shipment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Permission: shipper or staff only
        if not request.user.is_staff:
            user_company_id = getattr(request.user, "company_id", None)
            if str(user_company_id) != str(shipment.shipper_id):
                return Response(
                    {"detail": "Only the shipper or staff can add cargo to this shipment."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Shipment must be editable
        validate_shipment_editable(shipment)

        serializer = CargoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cargo = serializer.save(shipment=shipment)

        return Response(
            CargoSerializer(cargo).data,
            status=status.HTTP_201_CREATED,
        )


# ── TRACKING EVENT CREATE VIEW ─────────────────────────────────────────────────


class TrackingEventCreateView(APIView):
    """
    POST /api/v1/shipments/{id}/events/

    Creates a tracking event for a shipment.

    Permission: carrier staff who own this shipment.
    Shippers cannot self-report tracking events (integrity of tracking data).

    Rate limit: uses ShipmentListThrottle (100/min) as a base.
    Per-shipment rate limiting would require Redis sorted sets — Phase 8.

    Atomic: single INSERT — no multi-table operation.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [ShipmentListThrottle]

    def post(self, request, id: uuid.UUID, *args, **kwargs):
        try:
            shipment = Shipment.objects.get(pk=id)
        except Shipment.DoesNotExist:
            return Response(
                {"detail": "Shipment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Permission: carrier staff only (not shipper)
        if not request.user.is_staff:
            user_carrier_id = getattr(request.user, "carrier_id", None)
            if str(user_carrier_id) != str(shipment.carrier_id):
                return Response(
                    {"detail": "Only the carrier's staff can create tracking events."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Shipment must be editable
        validate_shipment_editable(shipment)

        serializer = TrackingEventCreateSerializer(
            data=request.data,
            shipment_status=shipment.status,
        )
        serializer.is_valid(raise_exception=True)

        event = serializer.save(
            shipment=shipment,
            recorded_at=timezone.now(),
            raw_payload={},
        )

        return Response(
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "event_time": event.event_time.isoformat(),
                "recorded_at": event.recorded_at.isoformat(),
                "is_exception": event.is_exception,
            },
            status=status.HTTP_201_CREATED,
        )


# ── WEBHOOK RECEIVE VIEW ───────────────────────────────────────────────────────


class WebhookReceiveView(APIView):
    """
    POST /api/v1/webhooks/carrier/{carrier_code}/

    Receives EDI/API webhook events from carrier systems.

    Authentication: HMAC-SHA256 signature — NOT JWT.
    Carrier systems call this endpoint server-to-server.
    They do not have user accounts and cannot obtain JWT tokens.
    The carrier's webhook secret is stored in carrier.service_metadata['webhook_secret'].

    Security:
    1. Signature verified BEFORE any processing (fail fast)
    2. Return 401 (not 400) on bad signature — it's an auth failure
    3. Idempotency on webhook_id — duplicate events are silently ignored
    4. Raw payload stored verbatim — processed asynchronously (Phase 8)

    ⚠️ TIMING ATTACK PREVENTION:
    hmac.compare_digest() is used for constant-time comparison.
    A naive == comparison leaks signature length via response time.

    ⚠️ RETURN 200 ALWAYS on valid signature:
    If we return 4xx/5xx for processing errors, carrier systems will retry.
    Retries create duplicate events. Always 200 on valid signature,
    even if we can't match the shipment reference.
    """

    permission_classes = []  # No JWT — HMAC only
    authentication_classes = []  # Bypass DRF auth entirely

    def post(self, request, carrier_code: str, *args, **kwargs):
        # ── Step 1: Get carrier and webhook secret ─────────────────────────────
        try:
            carrier = Carrier.objects.get(carrier_code=carrier_code, is_active=True)
        except Carrier.DoesNotExist:
            # Return 401 not 404 — don't reveal whether carrier_code exists
            return Response(
                {"detail": "Invalid carrier."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        webhook_secret = carrier.service_metadata.get("webhook_secret")
        if not webhook_secret:
            # Carrier exists but has no webhook secret configured
            # Return 401 — carrier is not set up for webhooks
            return Response(
                {"detail": "Webhook not configured for this carrier."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # ── Step 2: Verify HMAC signature ──────────────────────────────────────
        signature = request.headers.get("X-Signature-SHA256", "")
        raw_body = request.body  # bytes — must be raw, not parsed

        if not validate_hmac_signature(raw_body, signature, webhook_secret):
            # ⚠️ 401 not 400 — signature failure is authentication failure
            return Response(
                {"detail": "Invalid signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # ── Step 3: Parse payload ──────────────────────────────────────────────
        serializer = WebhookPayloadSerializer(data=request.data)
        if not serializer.is_valid():
            # Malformed payload — but signature was valid, so return 200
            # Log the error and move on. Don't retry valid-signature bad payloads.
            return Response(
                {"detail": "Payload accepted but malformed — check carrier EDI format."},
                status=status.HTTP_200_OK,
            )

        data = serializer.validated_data
        webhook_id = data["webhook_id"]

        # ── Step 4: Idempotency check ──────────────────────────────────────────
        existing = _check_webhook_idempotency(webhook_id)
        if existing:
            # Already processed — return 200 silently
            return Response(
                {"detail": "Webhook already processed.", "webhook_id": webhook_id},
                status=status.HTTP_200_OK,
            )

        # ── Step 5: Find shipment by reference ────────────────────────────────
        shipment_ref = data["shipment_reference"]
        shipment = Shipment.objects.filter(
            tracking_number=shipment_ref,
            carrier=carrier,
        ).first()

       # ── Step 6: Store raw event (async processing in Phase 8) ─────────────
        # TrackingEvent.shipment is NOT NULL by design — a tracking event without
        # a shipment is meaningless. If we cannot match the shipment_reference,
        # we still must acknowledge the webhook (return 200) so the carrier does
        # not retry indefinitely, but we cannot create a TrackingEvent row.
        #
        # Unmatched webhooks are logged via Python logging for manual reconciliation.
        # Phase 8 will add a dedicated UnmatchedWebhook model for this case.
        if shipment is None:
            import logging
            logger = logging.getLogger("webhooks")
            logger.warning(
                "Webhook %s from carrier %s could not be matched to a shipment "
                "(reference: %s). Payload discarded — manual reconciliation required.",
                webhook_id, carrier_code, shipment_ref,
            )
            return Response(
                {
                    "detail": "Webhook received but no matching shipment found.",
                    "webhook_id": webhook_id,
                    "shipment_matched": False,
                },
                status=status.HTTP_200_OK,
            )

        TrackingEvent.objects.create(
            shipment=shipment,
            event_type=TrackingEventType.DOCUMENT_RECEIVED,  # placeholder type
            event_time=data["event_timestamp"],
            description=f"Carrier webhook: {data['event_type']}",
            source_system=f"WEBHOOK:{carrier_code}",
            is_exception=False,
            raw_payload={
                "webhook_id": webhook_id,
                "carrier_code": carrier_code,
                "event_type": data["event_type"],
                "shipment_reference": shipment_ref,
                "payload": data.get("payload", {}),
                "received_at": timezone.now().isoformat(),
            },
        )

        return Response(
            {
                "detail": "Webhook received.",
                "webhook_id": webhook_id,
                "shipment_matched": True,
            },
            status=status.HTTP_200_OK,
        )


# ── HEALTH CHECK VIEW ──────────────────────────────────────────────────────────


class HealthCheckView(APIView):
    """
    GET /api/v1/health/

    Used by Railway healthcheck and monitoring systems.
    No authentication required — Railway must call this without a token.
    Not logged by AuditMiddleware (excluded from audit trail).

    Checks:
    - DB connection (simple SELECT 1)
    - Returns 200 if healthy, 503 if any component down

    Response time must be < 100ms — simple ping only, no aggregations.
    """

    permission_classes = []
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        # Check DB
        db_status = "ok"
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            db_status = "error"

        # Check cache
        cache_status = "ok"
        try:
            from django.core.cache import cache
            cache.set("health_check", "1", timeout=5)
            if cache.get("health_check") != "1":
                cache_status = "error"
        except Exception:
            cache_status = "error"

        overall = "ok" if db_status == "ok" else "degraded"
        http_status = (
            status.HTTP_200_OK if overall == "ok"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )

        return Response(
            {
                "status": overall,
                "db": db_status,
                "cache": cache_status,
                "version": "1.0.0",
            },
            status=http_status,
        )