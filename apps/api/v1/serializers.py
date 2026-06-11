"""
apps/api/v1/serializers.py

Global Trade & Logistics Analytics Platform — Phase 3: API Layer
Serializers are the contract between the ORM layer and the outside world.
Every field is explicit. No __all__. No surprise exposure.
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.logistics.models import Cargo, Carrier, Company, Port, Route, Shipment, TrackingEvent


# ── SUPPORTING SERIALIZERS ─────────────────────────────────────────────────────


class CargoSerializer(serializers.ModelSerializer):
    """
    Full cargo detail for use inside ShipmentDetailSerializer.
    Read-only: cargo is created via shipment booking workflow, not patched via API.
    """

    cargo_type_display = serializers.CharField(
        source="get_cargo_type_display", read_only=True
    )

    class Meta:
        model = Cargo
        fields = (
            "id",
            "cargo_type",
            "cargo_type_display",
            "description",
            "container_number",
            "gross_weight_kg",
            "net_weight_kg",
            "volume_cbm",
            "package_count",
            "package_type",
            "is_hazmat",
            "un_number",
            "temperature_min_c",
            "temperature_max_c",
        )
        read_only_fields = fields  # entire serializer is read-only at API level


class TrackingEventSerializer(serializers.ModelSerializer):
    """
    Tracking event for embedding in shipment detail.
    raw_payload excluded: internal audit field, not for API consumers.
    source_system excluded: internal infrastructure detail.
    """

    event_type_display = serializers.CharField(
        source="get_event_type_display", read_only=True
    )
    port_name = serializers.SerializerMethodField()

    class Meta:
        model = TrackingEvent
        fields = (
            "id",
            "event_type",
            "event_type_display",
            "event_time",
            "recorded_at",
            "port_name",
            "location_description",
            "description",
            "is_exception",
            "exception_resolved",
        )
        read_only_fields = fields

    def get_port_name(self, obj: TrackingEvent) -> str | None:
        if obj.port_id:
            return obj.port.port_name
        return None


# ── SHIPMENT SERIALIZERS ───────────────────────────────────────────────────────


class ShipmentListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list endpoints.

    Uses SerializerMethodField for computed/related fields instead of nested
    serializers. This is safe from N+1 only when the queryset is annotated with
    .with_carrier().with_route() — which ShipmentListView enforces.
    Without those select_related calls, each get_carrier_name() call would
    issue a separate DB query per row.

    Declared as read-only throughout: list endpoint is GET only.
    """

    carrier_name = serializers.SerializerMethodField()
    carrier_mode = serializers.SerializerMethodField()
    origin_port = serializers.SerializerMethodField()
    destination_port = serializers.SerializerMethodField()
    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )
    is_delayed = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Shipment
        fields = (
            "id",
            "tracking_number",
            "status",
            "status_display",
            "carrier_name",
            "carrier_mode",
            "origin_port",
            "destination_port",
            "declared_value",
            "currency_code",
            "departure_date",
            "estimated_arrival",
            "is_delayed",
        )
        read_only_fields = fields

    def get_carrier_name(self, obj: Shipment) -> str:
        # Safe because queryset uses .with_carrier() — no extra query
        return obj.carrier.carrier_name

    def get_carrier_mode(self, obj: Shipment) -> str:
        return obj.carrier.get_mode_display()

    def get_origin_port(self, obj: Shipment) -> dict:
        # Safe because queryset uses .with_route() — no extra query
        port = obj.route.origin_port
        return {"un_locode": port.un_locode, "port_name": port.port_name}

    def get_destination_port(self, obj: Shipment) -> dict:
        port = obj.route.destination_port
        return {"un_locode": port.un_locode, "port_name": port.port_name}


class ShipmentDetailSerializer(serializers.ModelSerializer):
    """
    Full shipment detail serializer.

    Nested serializers are safe here: single-object view always has
    select_related and prefetch_related applied by ShipmentDetailView.

    Read-only rationale per field group:
    - tracking_number: issued by system on booking; immutable after creation
    - carrier, route, shipper, consignee: set at booking; changes require
      a formal amendment workflow, not a PATCH on this endpoint
    - created_at, updated_at: system-managed timestamps
    - cargo_items: managed via dedicated cargo endpoints (Phase 5 scope)
    - transit_duration: computed annotation, not a model field
    - latest_event_time: computed annotation from subquery
    """

    status_display = serializers.CharField(
        source="get_status_display", read_only=True
    )
    carrier_name = serializers.CharField(
        source="carrier.carrier_name", read_only=True
    )
    carrier_mode = serializers.CharField(
        source="carrier.get_mode_display", read_only=True
    )
    shipper_name = serializers.CharField(
        source="shipper.legal_name", read_only=True
    )
    consignee_name = serializers.CharField(
        source="consignee.legal_name", read_only=True
    )
    cargo_items = CargoSerializer(many=True, read_only=True)
    transit_duration_seconds = serializers.SerializerMethodField()
    latest_event_time = serializers.DateTimeField(read_only=True, default=None)

    class Meta:
        model = Shipment
        fields = (
            "id",
            "tracking_number",
            "bill_of_lading",
            "house_bill_of_lading",
            "status",
            "status_display",
            "carrier_name",
            "carrier_mode",
            "shipper_name",
            "consignee_name",
            "origin_port_id",
            "destination_port_id",
            "declared_value",
            "freight_cost",
            "currency_code",
            "incoterms",
            "booking_date",
            "departure_date",
            "estimated_arrival",
            "actual_arrival",
            "transit_duration_seconds",
            "latest_event_time",
            "hs_codes",
            "tags",
            "notes",
            "cargo_items",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_transit_duration_seconds(self, obj: Shipment) -> int | None:
        duration = getattr(obj, "transit_duration", None)
        if duration is None:
            return None
        return int(duration.total_seconds())


# ── ANALYTICS SERIALIZERS ──────────────────────────────────────────────────────


class AnalyticsSummarySerializer(serializers.Serializer):
    """
    Serializes output of ShipmentAnalyticsManager.dashboard_summary().
    Not a ModelSerializer — dashboard_summary() returns a plain dict, not a model instance.

    coerce_to_string=False on DecimalField: we want numeric JSON values,
    not strings. DRF defaults to string for Decimal to avoid JS float precision
    issues — but our API clients are expected to handle Decimal-as-number correctly,
    and string representation breaks numeric aggregations on the client side.
    """

    total = serializers.IntegerField()
    active = serializers.IntegerField()
    delayed = serializers.IntegerField()
    total_declared_value = serializers.DecimalField(
        max_digits=22,
        decimal_places=2,
        coerce_to_string=False,
    )


class CarrierLeaderboardSerializer(serializers.Serializer):
    """
    Serializes one row from ShipmentAnalyticsManager.carrier_leaderboard().
    Plain Serializer — input is a list of dicts, not model instances.
    """

    carrier_id = serializers.UUIDField()
    carrier__carrier_name = serializers.CharField()
    shipment_count = serializers.IntegerField()
    delay_rate_pct = serializers.DecimalField(
        max_digits=6, decimal_places=2, coerce_to_string=False
    )
    avg_declared_value = serializers.DecimalField(
        max_digits=18, decimal_places=2, coerce_to_string=False
    )
    total_gross_weight_kg = serializers.DecimalField(
        max_digits=18, decimal_places=3, coerce_to_string=False
    )
    volume_rank = serializers.IntegerField()


class MonthlyVolumeTrendSerializer(serializers.Serializer):
    """
    Serializes one row from ShipmentQuerySet.monthly_volume_trend().
    """

    month = serializers.DateField()
    shipment_count = serializers.IntegerField()
    total_declared_value = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=False
    )
