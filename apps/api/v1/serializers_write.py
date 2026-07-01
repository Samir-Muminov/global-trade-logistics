"""
apps/api/v1/serializers_write.py

Write serializers for all mutating operations.

Separation from serializers.py (read) is intentional:
- Read serializers are optimised for fast list/detail rendering
- Write serializers are optimised for validation depth and atomic safety
- Mixing them creates ambiguity about what fields are writable

Every serializer here:
1. Validates domain invariants before touching the DB
2. Documents exactly which fields are writable and why
3. Returns clean, typed validated_data ready for atomic view operations
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.api.v1.validators import (
    generate_tracking_number,
    validate_carrier_active,
    validate_carrier_route_match,
    validate_event_type_for_status,
    validate_port_route_match,
    validate_shipment_editable,
    validate_status_transition,
)
from apps.logistics.models import (
    Cargo,
    CargoType,
    Carrier,
    Port,
    Route,
    Shipment,
    ShipmentStatus,
    TrackingEvent,
    TrackingEventType,
)


# ── SHIPMENT CREATE ────────────────────────────────────────────────────────────


class ShipmentCreateSerializer(serializers.Serializer):
    """
    Serializer for POST /api/v1/shipments/

    NOT a ModelSerializer — explicit fields give us full control over:
    - Which fields are accepted (no accidental mass assignment)
    - Validation order (carrier → route → ports, in dependency order)
    - Idempotency key handling
    - Auto-generation of tracking_number

    Writable fields:
        shipper_id       — must match request.user.company_id (enforced in view)
        consignee_id     — any active company
        carrier_id       — must be active
        route_id         — must belong to carrier_id
        origin_port_id   — must match route.origin_port
        destination_port_id — must match route.destination_port
        departure_date   — must be in the future
        declared_value   — optional, Decimal
        freight_cost     — optional, Decimal
        currency_code    — ISO 4217, default USD
        incoterms        — optional
        notes            — optional
        idempotency_key  — required, UUID, client-generated

    Auto-generated (not accepted from client):
        tracking_number  — GT-{8hex}
        status           — always starts as DRAFT
        bill_of_lading   — set by carrier later
    """

    # ── Party IDs ──────────────────────────────────────────────────────────────
    shipper_id = serializers.UUIDField(
        help_text="UUID of the shipper company. Must match your account's company."
    )
    consignee_id = serializers.UUIDField(
        help_text="UUID of the consignee company."
    )
    notify_party_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Optional UUID of the notify party company.",
    )

    # ── Transport ──────────────────────────────────────────────────────────────
    carrier_id = serializers.UUIDField(
        help_text="UUID of the active carrier."
    )
    route_id = serializers.UUIDField(
        help_text="UUID of the route. Must belong to the specified carrier."
    )
    origin_port_id = serializers.UUIDField(
        help_text="UUID of the origin port. Must match route's origin port."
    )
    destination_port_id = serializers.UUIDField(
        help_text="UUID of the destination port. Must match route's destination port."
    )

    # ── Schedule ───────────────────────────────────────────────────────────────
    departure_date = serializers.DateTimeField(
        help_text="Planned departure datetime (UTC). Must be in the future."
    )
    estimated_arrival = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Estimated arrival datetime (UTC). Must be after departure_date.",
    )

    # ── Financials ─────────────────────────────────────────────────────────────
    declared_value = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
        help_text="Declared cargo value in currency_code. Used for customs and insurance.",
    )
    freight_cost = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
        help_text="Agreed freight cost in currency_code.",
    )
    currency_code = serializers.CharField(
        max_length=3,
        default="USD",
        help_text="ISO 4217 currency code e.g. USD, EUR, CNY.",
    )

    # ── References ─────────────────────────────────────────────────────────────
    incoterms = serializers.CharField(
        max_length=3,
        required=False,
        allow_blank=True,
        default="",
        help_text="Incoterms 2020 code e.g. FOB, CIF, DAP.",
    )
    purchase_order_refs = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        default=list,
        help_text="PO numbers this shipment fulfils.",
    )
    hs_codes = serializers.ListField(
        child=serializers.CharField(max_length=10),
        required=False,
        default=list,
        help_text="HS tariff codes for contained commodities.",
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        default=list,
        help_text="Operational tags for internal workflows.",
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=10000,
        help_text="Internal notes. Not visible to carrier.",
    )

    # ── Idempotency ────────────────────────────────────────────────────────────
    idempotency_key = serializers.UUIDField(
        help_text=(
            "Client-generated UUID. If a request with this key was already "
            "processed, the original response is returned (HTTP 200). "
            "Generate a new UUID for each distinct shipment booking attempt."
        )
    )

    def validate_currency_code(self, value: str) -> str:
        if not value.isalpha() or len(value) != 3:
            raise serializers.ValidationError(
                "Currency code must be 3 uppercase letters (ISO 4217)."
            )
        return value.upper()

    def validate_departure_date(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError(
                "Departure date must be in the future."
            )
        return value

    def validate(self, attrs: dict) -> dict:
        """
        Cross-field validation in dependency order:
        1. Carrier must be active
        2. Route must belong to carrier
        3. Ports must match route
        4. ETA must be after departure
        """
        validate_carrier_active(attrs["carrier_id"])
        validate_carrier_route_match(attrs["carrier_id"], attrs["route_id"])
        validate_port_route_match(
            attrs["route_id"],
            attrs["origin_port_id"],
            attrs["destination_port_id"],
        )

        estimated_arrival = attrs.get("estimated_arrival")
        if estimated_arrival and estimated_arrival <= attrs["departure_date"]:
            raise serializers.ValidationError(
                {"estimated_arrival": "Estimated arrival must be after departure date."}
            )

        return attrs


# ── SHIPMENT STATUS UPDATE ─────────────────────────────────────────────────────


class ShipmentStatusUpdateSerializer(serializers.Serializer):
    """
    Serializer for PATCH /api/v1/shipments/{id}/status/

    Validates the status transition is legal according to the state machine.
    The actual DB update uses select_for_update() in the view — not here.
    Serializers do not touch the DB.

    Fields:
        new_status    — target status
        note          — optional note for the TrackingEvent created on transition
        event_location — optional location string for the TrackingEvent
    """

    new_status = serializers.ChoiceField(
        choices=ShipmentStatus.choices,
        help_text="Target status. Must be a valid transition from the current status.",
    )
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=2000,
        help_text="Optional note recorded in the tracking event.",
    )
    event_location = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=255,
        help_text="Location description for the tracking event.",
    )

    def __init__(self, *args, current_status: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_status = current_status

    def validate_new_status(self, value: str) -> str:
        if self._current_status:
            validate_status_transition(self._current_status, value)
        return value


# ── CARGO CREATE ───────────────────────────────────────────────────────────────


class CargoCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for POST /api/v1/shipments/{id}/cargo/

    Uses ModelSerializer for field definition convenience.
    write_only fields: shipment is set by the view from URL kwargs, not from request body.

    Domain validations at serializer level (even though DB constraints exist):
    - net_weight_kg ≤ gross_weight_kg → 400, not 500
    - is_hazmat=True requires un_number → 400, not 500
    - cargo_type=REEFER requires temperature_min_c → 400, not 500

    Validating at serializer level gives clients actionable 400 errors
    instead of opaque 500 DB constraint violations.
    """

    class Meta:
        model = Cargo
        fields = (
            "cargo_type",
            "description",
            "container_number",
            "seal_number",
            "gross_weight_kg",
            "net_weight_kg",
            "volume_cbm",
            "package_count",
            "package_type",
            "is_hazmat",
            "un_number",
            "imdg_class",
            "temperature_min_c",
            "temperature_max_c",
            "custom_attributes",
        )

    def validate_gross_weight_kg(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError("Gross weight must be positive.")
        return value

    def validate_un_number(self, value: str) -> str:
        if value and (not value.isdigit() or len(value) != 4):
            raise serializers.ValidationError(
                "UN number must be exactly 4 digits e.g. 1263."
            )
        return value

    def validate(self, attrs: dict) -> dict:
        # net ≤ gross
        net = attrs.get("net_weight_kg")
        gross = attrs.get("gross_weight_kg")
        if net is not None and gross is not None and net > gross:
            raise serializers.ValidationError(
                {"net_weight_kg": "Net weight cannot exceed gross weight."}
            )

        # hazmat requires UN number
        if attrs.get("is_hazmat") and not attrs.get("un_number"):
            raise serializers.ValidationError(
                {"un_number": "UN number is required for hazardous cargo."}
            )

        # REEFER requires temperature
        if attrs.get("cargo_type") == CargoType.REEFER:
            if attrs.get("temperature_min_c") is None:
                raise serializers.ValidationError(
                    {"temperature_min_c": "Temperature setpoint required for REEFER cargo."}
                )

        # Temperature range ordered
        temp_min = attrs.get("temperature_min_c")
        temp_max = attrs.get("temperature_max_c")
        if temp_min is not None and temp_max is not None and temp_max < temp_min:
            raise serializers.ValidationError(
                {"temperature_max_c": "Max temperature cannot be below min temperature."}
            )

        return attrs


# ── TRACKING EVENT CREATE ──────────────────────────────────────────────────────


class TrackingEventCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for POST /api/v1/shipments/{id}/events/

    Client supplies: event_type, event_time, port_id (optional),
                     location_description, description, is_exception.
    Server sets: recorded_at (auto), shipment_id (from URL), source_system.

    If is_exception=True: description becomes required (exception must be described).

    event_type is validated against current shipment status.
    """

    exception_description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        write_only=True,
        help_text=(
            "Required when is_exception=True. "
            "Describes the nature of the exception for ops team."
        ),
    )

    class Meta:
        model = TrackingEvent
        fields = (
            "event_type",
            "event_time",
            "port_id",
            "location_description",
            "description",
            "is_exception",
            "exception_description",
            "source_system",
        )
        extra_kwargs = {
            "port_id": {"required": False, "allow_null": True},
            "location_description": {"required": False, "allow_blank": True, "default": ""},
            "description": {"required": False, "allow_blank": True, "default": ""},
            "is_exception": {"default": False},
            "source_system": {"required": False, "allow_blank": True, "default": "API"},
        }

    def __init__(self, *args, shipment_status: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._shipment_status = shipment_status

    def validate_event_time(self, value):
        if value > timezone.now():
            raise serializers.ValidationError(
                "Event time cannot be in the future. "
                "Events are facts — they cannot be pre-recorded."
            )
        return value

    def validate(self, attrs: dict) -> dict:
        # Validate event type against current shipment status
        if self._shipment_status:
            validate_event_type_for_status(
                attrs["event_type"],
                self._shipment_status,
            )

        # Exception must have a description
        if attrs.get("is_exception") and not attrs.get("exception_description"):
            raise serializers.ValidationError(
                {
                    "exception_description": (
                        "Exception description is required when is_exception=True. "
                        "Describe what went wrong for the ops team."
                    )
                }
            )

        # Merge exception_description into description field
        if attrs.get("exception_description"):
            existing = attrs.get("description", "")
            exc_desc = attrs.pop("exception_description")
            attrs["description"] = f"[EXCEPTION] {exc_desc}" + (
                f"\n{existing}" if existing else ""
            )
        else:
            attrs.pop("exception_description", None)

        return attrs


# ── WEBHOOK PAYLOAD ────────────────────────────────────────────────────────────


class WebhookPayloadSerializer(serializers.Serializer):
    """
    Serializer for POST /api/v1/webhooks/carrier/{carrier_code}/

    Minimal validation — webhook payloads are saved as raw_payload first,
    then processed asynchronously. We only validate the envelope structure,
    not the full payload content (which varies by carrier EDI format).

    HMAC signature verification happens in the view BEFORE this serializer
    runs — if signature is invalid, serializer is never called.
    """

    webhook_id = serializers.CharField(
        max_length=128,
        help_text=(
            "Carrier-assigned unique ID for this webhook event. "
            "Used for idempotency — duplicate webhook_id = ignored."
        ),
    )
    event_type = serializers.CharField(
        max_length=64,
        help_text="Carrier-specific event type string.",
    )
    shipment_reference = serializers.CharField(
        max_length=64,
        help_text=(
            "Our tracking_number or the carrier's own reference. "
            "Used to match to a Shipment record."
        ),
    )
    event_timestamp = serializers.DateTimeField(
        help_text="When the event occurred at the carrier's system (UTC).",
    )
    payload = serializers.DictField(
        required=False,
        default=dict,
        help_text="Full carrier-specific payload. Stored verbatim for reprocessing.",
    )