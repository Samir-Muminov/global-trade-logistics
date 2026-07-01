"""
apps/api/v1/validators.py

Domain validators for write operations.

Design principle: validators raise ValidationError with field-specific messages.
They are pure functions — no side effects, no DB writes.
Every validator is testable in isolation.

Validators are called from serializers (field/object level)
and from views (before atomic operations begin).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from decimal import Decimal

from rest_framework.exceptions import ValidationError

from apps.logistics.models import (
    Carrier,
    Port,
    Route,
    Shipment,
    ShipmentStatus,
)

# ── Status Transition Machine ──────────────────────────────────────────────────

# Valid transitions: {from_status: {to_status, ...}}
# Any status can transition to CANCELLED or EXCEPTION (operational reality).
# DELIVERED and CANCELLED are terminal — no further transitions allowed.
VALID_TRANSITIONS: dict[str, set[str]] = {
    ShipmentStatus.DRAFT: {
        ShipmentStatus.BOOKED,
        ShipmentStatus.CANCELLED,
    },
    ShipmentStatus.BOOKED: {
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.CANCELLED,
        ShipmentStatus.EXCEPTION,
    },
    ShipmentStatus.IN_TRANSIT: {
        ShipmentStatus.AT_PORT,
        ShipmentStatus.DELIVERED,
        ShipmentStatus.CANCELLED,
        ShipmentStatus.EXCEPTION,
    },
    ShipmentStatus.AT_PORT: {
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.DELIVERED,
        ShipmentStatus.CANCELLED,
        ShipmentStatus.EXCEPTION,
    },
    ShipmentStatus.CUSTOMS_HOLD: {
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.AT_PORT,
        ShipmentStatus.DELIVERED,
        ShipmentStatus.CANCELLED,
        ShipmentStatus.EXCEPTION,
    },
    ShipmentStatus.EXCEPTION: {
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.AT_PORT,
        ShipmentStatus.DELIVERED,
        ShipmentStatus.CANCELLED,
    },
    # Terminal states — no outbound transitions
    ShipmentStatus.DELIVERED: set(),
    ShipmentStatus.CANCELLED: set(),
}

# Event types that are valid for each shipment status
# Used by TrackingEventCreateSerializer to validate event_type
VALID_EVENT_TYPES_FOR_STATUS: dict[str, set[str]] = {
    ShipmentStatus.DRAFT: {"DOC_RECEIVED"},
    ShipmentStatus.BOOKED: {"DOC_RECEIVED", "VESSEL_CHANGE"},
    ShipmentStatus.IN_TRANSIT: {
        "DEPARTURE", "ARRIVAL", "TRANSSHIPMENT", "DELAY",
        "EXCEPTION", "VESSEL_CHANGE", "DOC_RECEIVED",
    },
    ShipmentStatus.AT_PORT: {
        "ARRIVAL", "CUSTOMS_CLEARED", "CUSTOMS_HOLD",
        "EXCEPTION", "DOC_RECEIVED",
    },
    ShipmentStatus.CUSTOMS_HOLD: {
        "CUSTOMS_CLEARED", "EXCEPTION", "DOC_RECEIVED",
    },
    ShipmentStatus.EXCEPTION: {
        "DEPARTURE", "ARRIVAL", "EXCEPTION", "DOC_RECEIVED",
    },
    ShipmentStatus.DELIVERED: {"DELIVERED", "DOC_RECEIVED"},
    ShipmentStatus.CANCELLED: set(),
}


def validate_status_transition(
    current_status: str,
    new_status: str,
) -> None:
    """
    Validate that a status transition is allowed by the state machine.

    Raises ValidationError with human-readable message if transition is invalid.
    Does NOT raise if current == new (idempotent update — let caller decide).

    Examples:
        validate_status_transition("BOOKED", "IN_TRANSIT")  # OK
        validate_status_transition("DELIVERED", "BOOKED")   # raises
        validate_status_transition("CANCELLED", "IN_TRANSIT")  # raises
    """
    if current_status == new_status:
        return  # idempotent — caller decides if this is an error

    allowed = VALID_TRANSITIONS.get(current_status, set())

    if new_status not in allowed:
        if not allowed:
            raise ValidationError(
                {
                    "status": (
                        f"Shipment is in terminal status '{current_status}'. "
                        f"No further status transitions are allowed."
                    )
                }
            )
        raise ValidationError(
            {
                "status": (
                    f"Cannot transition from '{current_status}' to '{new_status}'. "
                    f"Allowed transitions from '{current_status}': "
                    f"{sorted(allowed) or 'none'}."
                )
            }
        )


def validate_carrier_route_match(
    carrier_id: uuid.UUID,
    route_id: uuid.UUID,
) -> None:
    """
    Validate that the route belongs to the specified carrier.

    A shipper cannot book a shipment where the route belongs to a different
    carrier than the one specified — this would create inconsistent data
    and break carrier invoicing.

    Raises ValidationError if route does not belong to carrier.
    Raises ValidationError if route does not exist (prevents IDOR via route_id).
    """
    try:
        route = Route.objects.select_related("carrier").get(id=route_id)
    except Route.DoesNotExist:
        raise ValidationError({"route_id": f"Route {route_id} does not exist."})

    if str(route.carrier_id) != str(carrier_id):
        raise ValidationError(
            {
                "route_id": (
                    f"Route {route_id} does not belong to carrier {carrier_id}. "
                    "The route and carrier must match."
                )
            }
        )

    if route.status not in ("ACTIVE", "SEASONAL"):
        raise ValidationError(
            {
                "route_id": (
                    f"Route {route_id} has status '{route.status}'. "
                    "Only ACTIVE or SEASONAL routes can be booked."
                )
            }
        )


def validate_port_route_match(
    route_id: uuid.UUID,
    origin_port_id: uuid.UUID,
    destination_port_id: uuid.UUID,
) -> None:
    """
    Validate that the origin and destination ports match the route definition.

    Prevents bookings where the shipper specifies ports that don't match
    the selected route — which would produce incorrect ETAs and break
    port call scheduling.
    """
    try:
        route = Route.objects.get(id=route_id)
    except Route.DoesNotExist:
        raise ValidationError({"route_id": f"Route {route_id} does not exist."})

    if str(route.origin_port_id) != str(origin_port_id):
        raise ValidationError(
            {
                "origin_port_id": (
                    f"Origin port {origin_port_id} does not match "
                    f"route's origin port {route.origin_port_id}."
                )
            }
        )

    if str(route.destination_port_id) != str(destination_port_id):
        raise ValidationError(
            {
                "destination_port_id": (
                    f"Destination port {destination_port_id} does not match "
                    f"route's destination port {route.destination_port_id}."
                )
            }
        )


def validate_shipment_editable(shipment: Shipment) -> None:
    """
    Validate that a shipment can be modified (cargo added, events created).

    Terminal-status shipments are immutable — cargo cannot be added to a
    delivered or cancelled shipment. Prevents data corruption and audit
    trail pollution.

    Raises ValidationError if shipment is in a terminal status.
    """
    terminal_statuses = {ShipmentStatus.DELIVERED, ShipmentStatus.CANCELLED}
    if shipment.status in terminal_statuses:
        raise ValidationError(
            {
                "shipment": (
                    f"Shipment {shipment.tracking_number} is in terminal status "
                    f"'{shipment.status}'. No modifications are allowed."
                )
            }
        )


def validate_carrier_active(carrier_id: uuid.UUID) -> None:
    """
    Validate that the carrier exists and is active.

    Raises ValidationError if carrier does not exist or is suspended.
    Called during shipment creation to prevent bookings with inactive carriers.
    """
    try:
        carrier = Carrier.objects.get(id=carrier_id)
    except Carrier.DoesNotExist:
        raise ValidationError({"carrier_id": f"Carrier {carrier_id} does not exist."})

    if not carrier.is_active:
        raise ValidationError(
            {
                "carrier_id": (
                    f"Carrier '{carrier.carrier_name}' is not active. "
                    "Cannot create shipments with a suspended carrier."
                )
            }
        )


def validate_event_type_for_status(
    event_type: str,
    shipment_status: str,
) -> None:
    """
    Validate that a tracking event type is valid for the current shipment status.

    A DEPARTURE event on a DELIVERED shipment makes no sense — it would
    corrupt the tracking timeline and confuse customers.

    Raises ValidationError if event_type is not valid for the current status.
    """
    allowed = VALID_EVENT_TYPES_FOR_STATUS.get(shipment_status, set())

    if event_type not in allowed:
        raise ValidationError(
            {
                "event_type": (
                    f"Event type '{event_type}' is not valid for shipment "
                    f"status '{shipment_status}'. "
                    f"Allowed event types: {sorted(allowed) or 'none'}."
                )
            }
        )


def validate_hmac_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """
    Validate HMAC-SHA256 webhook signature.

    Returns True if signature is valid, False otherwise.
    Uses hmac.compare_digest for timing-safe comparison.

    Signature format: "sha256=<hex_digest>"
    This matches GitHub, Stripe, and most webhook standards.

    Caller is responsible for returning 401 on False — not 400.
    401 is correct: the request is not authenticated (bad signature),
    not malformed (which would be 400).

    Security note: constant-time comparison prevents timing attacks
    where an attacker could determine correct signature bytes by
    measuring response time differences.
    """
    if not signature or not signature.startswith("sha256="):
        return False

    expected_sig = signature[7:]  # strip "sha256=" prefix
    computed = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected_sig)


def generate_tracking_number() -> str:
    """
    Generate a unique tracking number for a new shipment.

    Format: GT-{8 uppercase hex chars}
    Example: GT-A3F8C201

    8 hex chars = 4 bytes = ~4 billion combinations.
    Collision probability at 10M shipments ≈ 0.0012% — acceptable.
    If collision detected by DB unique constraint, caller retries.
    """
    suffix = uuid.uuid4().hex[:8].upper()
    return f"GT-{suffix}"