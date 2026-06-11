"""
apps/logistics/models.py
 
Global Trade & Logistics Analytics Platform — Phase 1: Fortress DB Architecture
Scale targets: 10M+ shipments | 500K+ routes | 50M+ tracking events | 1M+ users
"""
 
from __future__ import annotations
 
import uuid
from decimal import Decimal
 
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import BrinIndex, BTreeIndex, GinIndex, HashIndex
from django.db import models
from django.db.models import CheckConstraint, Q, UniqueConstraint
 
from apps.logistics.managers import (
    CarrierAnalyticsManager,
    CarrierManager,
    PortManager,
    ShipmentAnalyticsManager,
    ShipmentManager,
    TrackingEventManager,
)
 
# ── ENUMS ─────────────────────────────────────────────────────────────────────
 
 
class CompanyType(models.TextChoices):
    SHIPPER = "SHIPPER", "Shipper"
    CONSIGNEE = "CONSIGNEE", "Consignee"
    FREIGHT_FORWARDER = "FF", "Freight Forwarder"
    CUSTOMS_BROKER = "CB", "Customs Broker"
    THIRD_PARTY_LOGISTICS = "3PL", "3PL Provider"
 
 
class CarrierMode(models.TextChoices):
    SEA = "SEA", "Sea Freight"
    AIR = "AIR", "Air Freight"
    RAIL = "RAIL", "Rail Freight"
    ROAD = "ROAD", "Road Freight"
    MULTIMODAL = "MULTI", "Multimodal"
 
 
class ShipmentStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    BOOKED = "BOOKED", "Booked"
    IN_TRANSIT = "IN_TRANSIT", "In Transit"
    AT_PORT = "AT_PORT", "At Port"
    CUSTOMS_HOLD = "CUSTOMS_HOLD", "Customs Hold"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"
    EXCEPTION = "EXCEPTION", "Exception"
 
 
class TrackingEventType(models.TextChoices):
    DEPARTURE = "DEPARTURE", "Departure"
    ARRIVAL = "ARRIVAL", "Arrival"
    CUSTOMS_CLEARED = "CUSTOMS_CLEARED", "Customs Cleared"
    CUSTOMS_HOLD = "CUSTOMS_HOLD", "Customs Hold"
    TRANSSHIPMENT = "TRANSSHIPMENT", "Transshipment"
    DELAY = "DELAY", "Delay"
    EXCEPTION = "EXCEPTION", "Exception"
    DELIVERED = "DELIVERED", "Delivered"
    DOCUMENT_RECEIVED = "DOC_RECEIVED", "Document Received"
    VESSEL_CHANGE = "VESSEL_CHANGE", "Vessel Change"
 
 
class CargoType(models.TextChoices):
    GENERAL = "GENERAL", "General Cargo"
    BULK = "BULK", "Bulk"
    CONTAINER = "CONTAINER", "Containerized"
    BREAKBULK = "BREAKBULK", "Break Bulk"
    REEFER = "REEFER", "Refrigerated"
    HAZMAT = "HAZMAT", "Hazardous Material"
    OVERSIZED = "OVERSIZED", "Oversized / OOG"
    LIQUID = "LIQUID", "Liquid / Tanker"
 
 
class PortType(models.TextChoices):
    SEA = "SEA", "Seaport"
    AIR = "AIR", "Airport"
    DRY = "DRY", "Dry Port / ICD"
    MULTIMODAL = "MULTI", "Multimodal Hub"
 
 
class RouteStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    SEASONAL = "SEASONAL", "Seasonal"
    SUSPENDED = "SUSPENDED", "Suspended"
    DISCONTINUED = "DISCONTINUED", "Discontinued"
 
 
# ── ABSTRACT BASE ─────────────────────────────────────────────────────────────
 
 
class TimestampedModel(models.Model):
    """
    Abstract base: UUID PK + immutable created_at + mutable updated_at.
    UUID PKs eliminate auto-increment contention under high insert rates
    and make distributed ID generation safe without coordination.
    """
 
    id: models.UUIDField = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_column="id",
    )
    created_at: models.DateTimeField = models.DateTimeField(
        auto_now_add=True,
        db_index=False,  # indexed explicitly below with BRIN where appropriate
        db_column="created_at",
    )
    updated_at: models.DateTimeField = models.DateTimeField(
        auto_now=True,
        db_column="updated_at",
    )
 
    class Meta:
        abstract = True
 
 
# ── COMPANY ───────────────────────────────────────────────────────────────────
 
 
class Company(TimestampedModel):
    """
    Root entity for all trade participants.
    Cardinality: 1 Company → many Carriers, many Shipments (as shipper/consignee)
    """
 
    legal_name: models.CharField = models.CharField(
        max_length=255,
        db_column="legal_name",
    )
    trade_name: models.CharField = models.CharField(
        max_length=255,
        blank=True,
        db_column="trade_name",
    )
    company_type: models.CharField = models.CharField(
        max_length=10,
        choices=CompanyType.choices,
        db_column="company_type",
    )
    tax_id: models.CharField = models.CharField(
        max_length=64,
        unique=True,
        db_column="tax_id",
        help_text="VAT / EIN / TIN — jurisdiction-specific tax identifier",
    )
    duns_number: models.CharField = models.CharField(
        max_length=9,
        blank=True,
        db_column="duns_number",
        help_text="D-U-N-S 9-digit business identifier",
    )
    country_code: models.CharField = models.CharField(
        max_length=2,
        db_column="country_code",
        help_text="ISO 3166-1 alpha-2",
    )
    is_active: models.BooleanField = models.BooleanField(
        default=True,
        db_column="is_active",
    )
    metadata: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        db_column="metadata",
        help_text="Flexible per-company attributes: certifications, trade lanes, customs codes",
    )
 
    class Meta:
        db_table = "companies"
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        ordering = ["legal_name"]
        indexes = [
            # serves: filter by type + active status for carrier/partner lookups
            BTreeIndex(
                fields=["company_type", "is_active"],
                name="company_type_active_idx",
            ),
            # serves: exact country lookups for regional analytics dashboards
            HashIndex(
                fields=["country_code"],
                name="company_country_hash_idx",
            ),
            # serves: full-text / trigram search on company name (pg_trgm)
            GinIndex(
                fields=["legal_name"],
                name="company_legal_name_gin_idx",
                opclasses=["gin_trgm_ops"],
            ),
            # serves: JSONB containment queries (certifications, tags, etc.)
            GinIndex(
                fields=["metadata"],
                name="company_metadata_gin_idx",
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(company_type__in=[c[0] for c in CompanyType.choices]),
                name="company_company_type_valid",
            ),
            CheckConstraint(
                condition=Q(country_code__regex=r"^[A-Z]{2}$"),
                name="company_country_code_iso2",
            ),
        ]
 
    def __str__(self) -> str:
        return f"{self.legal_name} ({self.get_company_type_display()})"
 
    def __repr__(self) -> str:
        return f"<Company id={self.id} legal_name={self.legal_name!r} type={self.company_type}>"
 
 
# ── PORT ──────────────────────────────────────────────────────────────────────
 
 
class Port(TimestampedModel):
    """
    Physical or logical trade node (seaport, airport, ICD).
    Cardinality: 1 Port → many PortCalls, many RouteLegs (as origin/destination)
    """
 
    un_locode: models.CharField = models.CharField(
        max_length=5,
        unique=True,
        db_column="un_locode",
        help_text="UN/LOCODE 5-character code e.g. USLAX",
    )
    iata_code: models.CharField = models.CharField(
        max_length=3,
        blank=True,
        db_column="iata_code",
        help_text="IATA 3-letter airport code; blank for seaports",
    )
    port_name: models.CharField = models.CharField(
        max_length=255,
        db_column="port_name",
    )
    country_code: models.CharField = models.CharField(
        max_length=2,
        db_column="country_code",
        help_text="ISO 3166-1 alpha-2",
    )
    port_type: models.CharField = models.CharField(
        max_length=10,
        choices=PortType.choices,
        db_column="port_type",
    )
    latitude: models.DecimalField = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        db_column="latitude",
    )
    longitude: models.DecimalField = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        db_column="longitude",
    )
    timezone: models.CharField = models.CharField(
        max_length=64,
        db_column="timezone",
        help_text="IANA timezone identifier e.g. America/Los_Angeles",
    )
    is_active: models.BooleanField = models.BooleanField(
        default=True,
        db_column="is_active",
    )
    objects = PortManager()
 
    class Meta:
        db_table = "ports"
        verbose_name = "Port"
        verbose_name_plural = "Ports"
        ordering = ["port_name"]
        indexes = [
            # serves: exact code lookup during shipment booking / EDI ingestion
            HashIndex(fields=["un_locode"], name="port_unlocode_hash_idx"),
            # serves: exact IATA lookup for air freight event matching
            HashIndex(fields=["iata_code"], name="port_iata_hash_idx"),
            # serves: filter all active ports by type for routing engine
            BTreeIndex(
                fields=["port_type", "is_active"],
                name="port_type_active_idx",
            ),
            # serves: geo-bounding-box queries for map visualizations
            BTreeIndex(
                fields=["country_code", "port_type"],
                name="port_country_type_idx",
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(latitude__gte=-90) & Q(latitude__lte=90),
                name="port_latitude_valid_range",
            ),
            CheckConstraint(
                condition=Q(longitude__gte=-180) & Q(longitude__lte=180),
                name="port_longitude_valid_range",
            ),
            CheckConstraint(
                condition=Q(country_code__regex=r"^[A-Z]{2}$"),
                name="port_country_code_iso2",
            ),
        ]
 
    def __str__(self) -> str:
        return f"{self.port_name} ({self.un_locode})"
 
    def __repr__(self) -> str:
        return f"<Port id={self.id} un_locode={self.un_locode!r} type={self.port_type}>"
 
 
# ── CARRIER ───────────────────────────────────────────────────────────────────
 
 
class Carrier(TimestampedModel):
    """
    Transport operator (shipping line, airline, rail operator, trucking company).
    Cardinality: 1 Company → many Carriers | 1 Carrier → many Shipments
    """
 
    company: models.ForeignKey = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        # PROTECT: carriers have financial/legal history; hard-delete forbidden
        related_name="carriers",
        db_column="company_id",
    )
    carrier_code: models.CharField = models.CharField(
        max_length=10,
        unique=True,
        db_column="carrier_code",
        help_text="SCAC (road/rail), IATA numeric (air), or custom code (sea)",
    )
    carrier_name: models.CharField = models.CharField(
        max_length=255,
        db_column="carrier_name",
    )
    mode: models.CharField = models.CharField(
        max_length=10,
        choices=CarrierMode.choices,
        db_column="mode",
    )
    imo_number: models.CharField = models.CharField(
        max_length=7,
        blank=True,
        db_column="imo_number",
        help_text="IMO number for shipping lines; blank for non-sea carriers",
    )
    is_active: models.BooleanField = models.BooleanField(
        default=True,
        db_column="is_active",
    )
    hub_ports: ArrayField = ArrayField(
        models.CharField(max_length=5),
        default=list,
        blank=True,
        db_column="hub_ports",
        help_text="UN/LOCODE array of primary hub ports/airports for this carrier",
    )
    service_metadata: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        db_column="service_metadata",
        help_text="Service strings, alliance membership, vessel fleet details",
    )
    # ── Managers ──────────────────────────────────────────────────────────────
    objects = CarrierManager()
    analytics = CarrierAnalyticsManager()
 
    class Meta:
        db_table = "carriers"
        verbose_name = "Carrier"
        verbose_name_plural = "Carriers"
        ordering = ["carrier_name"]
        indexes = [
            # serves: exact SCAC/IATA code lookup during EDI parsing (high frequency)
            HashIndex(fields=["carrier_code"], name="carrier_code_hash_idx"),
            # serves: exact IMO lookup for vessel tracking integrations
            HashIndex(fields=["imo_number"], name="carrier_imo_hash_idx"),
            # serves: filter active carriers by mode for booking engine
            BTreeIndex(
                fields=["mode", "is_active"],
                name="carrier_mode_active_idx",
            ),
            # serves: array containment — "which carriers hub through USLAX?"
            GinIndex(fields=["hub_ports"], name="carrier_hub_ports_gin_idx"),
            # serves: JSONB service/alliance attribute queries
            GinIndex(
                fields=["service_metadata"],
                name="carrier_svc_meta_gin_idx",
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(mode__in=[c[0] for c in CarrierMode.choices]),
                name="carrier_mode_valid",
            ),
            # IMO numbers are exactly 7 digits; enforce at DB level
            CheckConstraint(
                condition=Q(imo_number="") | Q(imo_number__regex=r"^\d{7}$"),
                name="carrier_imo_number_format",
            ),
        ]
 
    def __str__(self) -> str:
        return f"{self.carrier_name} [{self.carrier_code}] ({self.get_mode_display()})"
 
    def __repr__(self) -> str:
        return f"<Carrier id={self.id} code={self.carrier_code!r} mode={self.mode}>"
 
 
# ── ROUTE ─────────────────────────────────────────────────────────────────────
 
 
class Route(TimestampedModel):
    """
    A defined trade lane between origin and destination ports/airports.
    Cardinality: 1 Carrier → many Routes | 1 Route → many Shipments
    Routes are logical definitions; PortCalls are physical execution instances.
    """
 
    carrier: models.ForeignKey = models.ForeignKey(
        Carrier,
        on_delete=models.PROTECT,
        related_name="routes",
        db_column="carrier_id",
    )
    origin_port: models.ForeignKey = models.ForeignKey(
        Port,
        on_delete=models.PROTECT,
        related_name="outbound_routes",
        db_column="origin_port_id",
    )
    destination_port: models.ForeignKey = models.ForeignKey(
        Port,
        on_delete=models.PROTECT,
        related_name="inbound_routes",
        db_column="destination_port_id",
    )
    route_code: models.CharField = models.CharField(
        max_length=32,
        unique=True,
        db_column="route_code",
        help_text="Internal route identifier e.g. CNSGH-USLAX-MSC-W1",
    )
    status: models.CharField = models.CharField(
        max_length=16,
        choices=RouteStatus.choices,
        default=RouteStatus.ACTIVE,
        db_column="status",
    )
    transit_days_min: models.PositiveSmallIntegerField = (
        models.PositiveSmallIntegerField(
            db_column="transit_days_min",
        )
    )
    transit_days_max: models.PositiveSmallIntegerField = (
        models.PositiveSmallIntegerField(
            db_column="transit_days_max",
        )
    )
    # Transshipment ports visited between origin and destination
    transshipment_ports: ArrayField = ArrayField(
        models.CharField(max_length=5),
        default=list,
        blank=True,
        db_column="transshipment_ports",
        help_text="Ordered array of UN/LOCODE transshipment hubs",
    )
    weekly_frequency: models.PositiveSmallIntegerField = (
        models.PositiveSmallIntegerField(
            default=1,
            db_column="weekly_frequency",
            help_text="Number of sailings/flights per week",
        )
    )
    effective_from: models.DateField = models.DateField(db_column="effective_from")
    effective_until: models.DateField = models.DateField(
        null=True,
        blank=True,
        db_column="effective_until",
        help_text="NULL means indefinitely active",
    )
 
    class Meta:
        db_table = "routes"
        verbose_name = "Route"
        verbose_name_plural = "Routes"
        ordering = ["carrier", "route_code"]
        indexes = [
            # serves: exact route_code lookup during shipment booking
            HashIndex(fields=["route_code"], name="route_code_hash_idx"),
            # serves: "find active routes for carrier X" — booking engine hot path
            BTreeIndex(
                fields=["carrier_id", "status"],
                name="route_carrier_status_idx",
            ),
            # serves: "routes from port A to port B" — trade lane discovery
            BTreeIndex(
                fields=["origin_port_id", "destination_port_id", "status"],
                name="route_lane_status_idx",
            ),
            # serves: effective date range filtering for schedule validity
            BTreeIndex(
                fields=["effective_from", "effective_until"],
                name="route_effective_range_idx",
            ),
            # serves: transshipment port containment — "routes via SGSIN"
            GinIndex(
                fields=["transshipment_ports"],
                name="route_transshipment_gin_idx",
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(transit_days_min__gt=0),
                name="route_transit_days_min_positive",
            ),
            CheckConstraint(
                condition=Q(transit_days_max__gte=models.F("transit_days_min")),
                name="route_transit_days_max_gte_min",
            ),
            CheckConstraint(
                condition=Q(weekly_frequency__gte=1) & Q(weekly_frequency__lte=21),
                name="route_weekly_frequency_sane",
            ),
            CheckConstraint(
                condition=Q(effective_until__isnull=True)
                | Q(effective_until__gt=models.F("effective_from")),
                name="route_effective_dates_ordered",
            ),
            # A carrier cannot duplicate the same logical lane under the same code
            UniqueConstraint(
                fields=["carrier_id", "origin_port_id", "destination_port_id"],
                condition=Q(status=RouteStatus.ACTIVE),
                name="route_carrier_active_lane_unique",
            ),
        ]
 
    def __str__(self) -> str:
        return f"{self.route_code}: {self.origin_port_id} → {self.destination_port_id}"
 
    def __repr__(self) -> str:
        return (
            f"<Route id={self.id} code={self.route_code!r} status={self.status}>"
        )
 
 
# ── SHIPMENT ──────────────────────────────────────────────────────────────────
 
 
class Shipment(TimestampedModel):
    """
    Central fact table. Every financial, compliance, and operational record
    links here. Designed for 10M+ rows with BRIN on timestamps for
    time-range queries across append-only insert patterns.
 
    Cardinality:
      1 Company (shipper)    → many Shipments
      1 Company (consignee)  → many Shipments
      1 Carrier              → many Shipments
      1 Route                → many Shipments
      1 Shipment             → many TrackingEvents
      1 Shipment             → many Cargo items
    """
 
    # ── Parties ───────────────────────────────────────────────────────────────
    shipper: models.ForeignKey = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="outbound_shipments",
        db_column="shipper_id",
    )
    consignee: models.ForeignKey = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inbound_shipments",
        db_column="consignee_id",
    )
    notify_party: models.ForeignKey = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        # SET_NULL: notify party is optional and operationally non-critical
        null=True,
        blank=True,
        related_name="notify_shipments",
        db_column="notify_party_id",
    )
 
    # ── Transport ─────────────────────────────────────────────────────────────
    carrier: models.ForeignKey = models.ForeignKey(
        Carrier,
        on_delete=models.PROTECT,
        related_name="shipments",
        db_column="carrier_id",
    )
    route: models.ForeignKey = models.ForeignKey(
        Route,
        on_delete=models.PROTECT,
        related_name="shipments",
        db_column="route_id",
    )
    origin_port: models.ForeignKey = models.ForeignKey(
        Port,
        on_delete=models.PROTECT,
        related_name="departing_shipments",
        db_column="origin_port_id",
    )
    destination_port: models.ForeignKey = models.ForeignKey(
        Port,
        on_delete=models.PROTECT,
        related_name="arriving_shipments",
        db_column="destination_port_id",
    )
 
    # ── Reference Numbers ─────────────────────────────────────────────────────
    tracking_number: models.CharField = models.CharField(
        max_length=64,
        unique=True,
        db_column="tracking_number",
        help_text="Public-facing tracking reference (customer-visible)",
    )
    bill_of_lading: models.CharField = models.CharField(
        max_length=64,
        blank=True,
        db_column="bill_of_lading",
        help_text="Master B/L number issued by carrier",
    )
    house_bill_of_lading: models.CharField = models.CharField(
        max_length=64,
        blank=True,
        db_column="house_bill_of_lading",
        help_text="House B/L issued by freight forwarder",
    )
    purchase_order_refs: ArrayField = ArrayField(
        models.CharField(max_length=64),
        default=list,
        blank=True,
        db_column="purchase_order_refs",
        help_text="PO numbers this shipment fulfils (multi-PO consolidations)",
    )
 
    # ── Status & Dates ────────────────────────────────────────────────────────
    status: models.CharField = models.CharField(
        max_length=16,
        choices=ShipmentStatus.choices,
        default=ShipmentStatus.DRAFT,
        db_column="status",
    )
    booking_date: models.DateField = models.DateField(
        null=True,
        blank=True,
        db_column="booking_date",
    )
    departure_date: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        db_column="departure_date",
        help_text="Actual or estimated departure (ETD)",
    )
    estimated_arrival: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        db_column="estimated_arrival",
        help_text="ETA at destination port",
    )
    actual_arrival: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        db_column="actual_arrival",
        help_text="ATA — set on final delivery event",
    )
 
    # ── Financials ────────────────────────────────────────────────────────────
    declared_value: models.DecimalField = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        db_column="declared_value",
        help_text="Cargo declared value in USD for customs/insurance",
    )
    freight_cost: models.DecimalField = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        db_column="freight_cost",
        help_text="Agreed freight rate in USD",
    )
    currency_code: models.CharField = models.CharField(
        max_length=3,
        default="USD",
        db_column="currency_code",
        help_text="ISO 4217 currency code",
    )
 
    # ── Metadata ──────────────────────────────────────────────────────────────
    incoterms: models.CharField = models.CharField(
        max_length=3,
        blank=True,
        db_column="incoterms",
        help_text="Incoterms 2020 code e.g. FOB, CIF, DAP",
    )
    hs_codes: ArrayField = ArrayField(
        models.CharField(max_length=10),
        default=list,
        blank=True,
        db_column="hs_codes",
        help_text="HS tariff codes for contained commodities",
    )
    tags: ArrayField = ArrayField(
        models.CharField(max_length=64),
        default=list,
        blank=True,
        db_column="tags",
        help_text="Operational tags: priority, project, lane",
    )
    custom_attributes: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        db_column="custom_attributes",
        help_text="Flexible shipper/forwarder defined attributes",
    )
    notes: models.TextField = models.TextField(
        blank=True,
        db_column="notes",
    )
    # ── Managers ──────────────────────────────────────────────────────────────
    objects = ShipmentManager()
    analytics = ShipmentAnalyticsManager()
 
    class Meta:
        db_table = "shipments"
        verbose_name = "Shipment"
        verbose_name_plural = "Shipments"
        ordering = ["-created_at"]
        indexes = [
            # serves: exact tracking number lookup — highest-frequency public API call
            HashIndex(
                fields=["tracking_number"],
                name="shipment_tracking_hash_idx",
            ),
            # serves: exact B/L lookup during document processing / EDI matching
            HashIndex(
                fields=["bill_of_lading"],
                name="shipment_bol_hash_idx",
            ),
            # serves: "all active shipments for carrier X" — operations dashboard
            BTreeIndex(
                fields=["carrier_id", "status"],
                name="shipment_carrier_status_idx",
            ),
            # serves: "shipper's in-transit shipments sorted by ETA" — customer portal
            BTreeIndex(
                fields=["shipper_id", "status", "estimated_arrival"],
                name="shipment_shipper_status_eta_idx",
            ),
            # serves: time-range scans on departure_date for schedule analytics
            BTreeIndex(
                fields=["departure_date"],
                name="shipment_departure_date_idx",
            ),
            # serves: ETA window queries — "arriving this week" for port planners
            BTreeIndex(
                fields=["estimated_arrival"],
                name="shipment_estimated_arrival_idx",
            ),
            # serves: partial — hot operational subset; avoids scanning terminal states
            BTreeIndex(
                fields=["carrier_id", "estimated_arrival"],
                condition=Q(
                    status__in=[
                        ShipmentStatus.BOOKED,
                        ShipmentStatus.IN_TRANSIT,
                        ShipmentStatus.AT_PORT,
                    ]
                ),
                name="shipment_active_carr_eta_idx",
            ),
            # serves: partial — exception queue for ops team
            BTreeIndex(
                fields=["created_at"],
                condition=Q(
                    status__in=[
                        ShipmentStatus.EXCEPTION,
                        ShipmentStatus.CUSTOMS_HOLD,
                    ]
                ),
                name="shipment_exception_idx",
            ),
            # serves: JSONB attribute queries (project codes, custom lane tags)
            GinIndex(
                fields=["custom_attributes"],
                name="shipment_custom_attr_gin_idx",
            ),
            # serves: HS code containment — "shipments carrying HS 8471.30"
            GinIndex(fields=["hs_codes"], name="shipment_hs_codes_gin_idx"),
            # serves: tag-based filtering — internal ops workflows
            GinIndex(fields=["tags"], name="shipment_tags_gin_idx"),
            # serves: append-only time-range scans on created_at (10M+ rows)
            # BRIN is 10-100x smaller than BTree for correlated timestamp columns
            BrinIndex(
                fields=["created_at"],
                name="shipment_created_at_brin_idx",
                pages_per_range=128,
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(status__in=[c[0] for c in ShipmentStatus.choices]),
                name="shipment_status_valid",
            ),
            CheckConstraint(
                condition=Q(declared_value__isnull=True)
                | Q(declared_value__gte=Decimal("0")),
                name="shipment_declared_value_non_negative",
            ),
            CheckConstraint(
                condition=Q(freight_cost__isnull=True)
                | Q(freight_cost__gte=Decimal("0")),
                name="shipment_freight_cost_non_negative",
            ),
            CheckConstraint(
                condition=Q(estimated_arrival__isnull=True)
                | Q(departure_date__isnull=True)
                | Q(estimated_arrival__gt=models.F("departure_date")),
                name="shipment_arrival_after_departure",
            ),
            CheckConstraint(
                condition=Q(actual_arrival__isnull=True)
                | Q(departure_date__isnull=True)
                | Q(actual_arrival__gte=models.F("departure_date")),
                name="shipment_actual_arrival_after_departure",
            ),
            CheckConstraint(
                condition=Q(currency_code__regex=r"^[A-Z]{3}$"),
                name="shipment_currency_code_iso4217",
            ),
            # B/L numbers must be unique when non-empty (sparse unique)
            UniqueConstraint(
                fields=["bill_of_lading"],
                condition=~Q(bill_of_lading=""),
                name="shipment_bol_unique_nonempty",
            ),
        ]
 
    def __str__(self) -> str:
        return f"Shipment {self.tracking_number} [{self.get_status_display()}]"
 
    def __repr__(self) -> str:
        return (
            f"<Shipment id={self.id} tracking={self.tracking_number!r} "
            f"status={self.status} carrier={self.carrier_id}>"
        )
 
 
# ── CARGO ─────────────────────────────────────────────────────────────────────
 
 
class Cargo(TimestampedModel):
    """
    A physical cargo unit within a shipment (one container, one pallet, one lot).
    A Shipment may contain multiple Cargo records (LCL consolidations).
 
    Cardinality: 1 Shipment → many Cargo items
    """
 
    shipment: models.ForeignKey = models.ForeignKey(
        Shipment,
        on_delete=models.CASCADE,
        # CASCADE: cargo has no meaning without its parent shipment
        related_name="cargo_items",
        db_column="shipment_id",
    )
    cargo_type: models.CharField = models.CharField(
        max_length=12,
        choices=CargoType.choices,
        db_column="cargo_type",
    )
    description: models.CharField = models.CharField(
        max_length=512,
        db_column="description",
        help_text="Commodity description for customs and B/L",
    )
    container_number: models.CharField = models.CharField(
        max_length=11,
        blank=True,
        db_column="container_number",
        help_text="ISO 6346 container number e.g. MSCU1234567",
    )
    seal_number: models.CharField = models.CharField(
        max_length=32,
        blank=True,
        db_column="seal_number",
    )
 
    # ── Physical Measurements ─────────────────────────────────────────────────
    gross_weight_kg: models.DecimalField = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        db_column="gross_weight_kg",
        help_text="Gross weight in kilograms; 3dp for sub-gram precision (pharma/jewellery)",
    )
    net_weight_kg: models.DecimalField = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        db_column="net_weight_kg",
    )
    volume_cbm: models.DecimalField = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        db_column="volume_cbm",
        help_text="Volume in cubic meters; 4dp for LCL rating precision",
    )
    package_count: models.PositiveIntegerField = models.PositiveIntegerField(
        default=1,
        db_column="package_count",
    )
    package_type: models.CharField = models.CharField(
        max_length=32,
        blank=True,
        db_column="package_type",
        help_text="UNECE Rec 21 package type code e.g. CT (Carton), PL (Pallet)",
    )
 
    # ── Hazmat / Regulatory ───────────────────────────────────────────────────
    is_hazmat: models.BooleanField = models.BooleanField(
        default=False,
        db_column="is_hazmat",
    )
    un_number: models.CharField = models.CharField(
        max_length=4,
        blank=True,
        db_column="un_number",
        help_text="UN number for hazardous goods e.g. 1263 (paint)",
    )
    imdg_class: models.CharField = models.CharField(
        max_length=8,
        blank=True,
        db_column="imdg_class",
        help_text="IMDG hazard class e.g. 3, 6.1, 8",
    )
 
    # ── Temperature / Reefer ──────────────────────────────────────────────────
    temperature_min_c: models.DecimalField = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        db_column="temperature_min_c",
        help_text="Min setpoint °C for reefer cargo",
    )
    temperature_max_c: models.DecimalField = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        db_column="temperature_max_c",
    )
 
    custom_attributes: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        db_column="custom_attributes",
        help_text="Dangerous goods placards, CITES permits, phytosanitary data",
    )
 
    class Meta:
        db_table = "cargo"
        verbose_name = "Cargo"
        verbose_name_plural = "Cargo Items"
        indexes = [
            # serves: exact container number lookup — vessel manifest matching
            HashIndex(
                fields=["container_number"],
                name="cargo_container_number_hash_idx",
            ),
            # serves: "all hazmat cargo in transit" — compliance reporting
            BTreeIndex(
                fields=["shipment_id", "is_hazmat"],
                name="cargo_shipment_hazmat_idx",
            ),
            # serves: cargo type aggregations for analytics (weight by type)
            BTreeIndex(
                fields=["cargo_type"],
                name="cargo_type_idx",
            ),
            # serves: weight range queries for load planning / vessel stowage
            BTreeIndex(
                fields=["gross_weight_kg"],
                name="cargo_gross_weight_idx",
            ),
            # serves: reefer cargo queries — "all cold chain units below -18°C"
            BTreeIndex(
                fields=["temperature_min_c"],
                condition=Q(temperature_min_c__isnull=False),
                name="cargo_reefer_temp_partial_idx",
            ),
            # serves: JSONB permit/certification containment queries
            GinIndex(
                fields=["custom_attributes"],
                name="cargo_custom_attr_gin_idx",
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(gross_weight_kg__gt=0),
                name="cargo_gross_weight_kg_positive",
            ),
            CheckConstraint(
                condition=Q(net_weight_kg__isnull=True)
                | Q(net_weight_kg__gt=0),
                name="cargo_net_weight_kg_positive",
            ),
            CheckConstraint(
                condition=Q(net_weight_kg__isnull=True)
                | Q(net_weight_kg__lte=models.F("gross_weight_kg")),
                name="cargo_net_weight_lte_gross",
            ),
            CheckConstraint(
                condition=Q(volume_cbm__isnull=True) | Q(volume_cbm__gt=0),
                name="cargo_volume_cbm_positive",
            ),
            CheckConstraint(
                condition=Q(package_count__gt=0),
                name="cargo_package_count_positive",
            ),
            # Hazmat cargo must carry a UN number
            CheckConstraint(
                condition=~Q(is_hazmat=True) | ~Q(un_number=""),
                name="cargo_hazmat_requires_un_number",
            ),
            # Reefer temp range must be logically ordered
            CheckConstraint(
                condition=Q(temperature_min_c__isnull=True)
                | Q(temperature_max_c__isnull=True)
                | Q(temperature_max_c__gte=models.F("temperature_min_c")),
                name="cargo_temperature_range_ordered",
            ),
            # UN numbers are exactly 4 digits
            CheckConstraint(
                condition=Q(un_number="") | Q(un_number__regex=r"^\d{4}$"),
                name="cargo_un_number_format",
            ),
        ]
 
    def __str__(self) -> str:
        return f"Cargo {self.container_number or self.id} — {self.get_cargo_type_display()}"
 
    def __repr__(self) -> str:
        return (
            f"<Cargo id={self.id} shipment={self.shipment_id} "
            f"type={self.cargo_type} weight={self.gross_weight_kg}kg>"
        )
 
 
# ── TRACKING EVENT ────────────────────────────────────────────────────────────
 
 
class TrackingEvent(TimestampedModel):
    """
    Immutable append-only log of every status change for a shipment.
    This is the highest-volume table: 50M+ rows.
 
    Design decisions:
    - No update path: events are facts; corrections are new events with supersedes_id
    - BRIN on event_time: insert-time correlation makes BRIN 100x smaller than BTree
    - Partial index on unresolved exceptions for ops dashboards
    - No FK to Cargo: event granularity is shipment-level; cargo-level events use metadata
 
    Cardinality: 1 Shipment → many TrackingEvents | 1 Port → many TrackingEvents
    """
 
    shipment: models.ForeignKey = models.ForeignKey(
        Shipment,
        on_delete=models.CASCADE,
        related_name="tracking_events",
        db_column="shipment_id",
    )
    port: models.ForeignKey = models.ForeignKey(
        Port,
        on_delete=models.PROTECT,
        related_name="tracking_events",
        db_column="port_id",
        null=True,
        blank=True,
        help_text="Port where event occurred; NULL for non-port events",
    )
    supersedes: models.ForeignKey = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
        db_column="supersedes_id",
        help_text="Points to the event this one corrects; enables audit trail",
    )
 
    event_type: models.CharField = models.CharField(
        max_length=20,
        choices=TrackingEventType.choices,
        db_column="event_type",
    )
    event_time: models.DateTimeField = models.DateTimeField(
        db_column="event_time",
        help_text="When the event physically occurred (not when it was recorded)",
    )
    recorded_at: models.DateTimeField = models.DateTimeField(
        auto_now_add=True,
        db_column="recorded_at",
        help_text="When this record was inserted (system time)",
    )
 
    location_description: models.CharField = models.CharField(
        max_length=255,
        blank=True,
        db_column="location_description",
        help_text="Free-text location for non-port events (e.g. truck GPS position)",
    )
    description: models.TextField = models.TextField(
        blank=True,
        db_column="description",
    )
    is_exception: models.BooleanField = models.BooleanField(
        default=False,
        db_column="is_exception",
    )
    exception_resolved: models.BooleanField = models.BooleanField(
        default=False,
        db_column="exception_resolved",
    )
    source_system: models.CharField = models.CharField(
        max_length=64,
        blank=True,
        db_column="source_system",
        help_text="EDI system, API provider, or manual entry identifier",
    )
    raw_payload: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        db_column="raw_payload",
        help_text="Original message from carrier API/EDI for audit and reprocessing",
    )
    objects = TrackingEventManager()
 
    class Meta:
        db_table = "tracking_events"
        verbose_name = "Tracking Event"
        verbose_name_plural = "Tracking Events"
        # Default: most recent first within a shipment's timeline
        ordering = ["shipment_id", "-event_time"]
        indexes = [
            # serves: "all events for shipment X ordered by time" — shipment timeline (hot path)
            BTreeIndex(
                fields=["shipment_id", "event_time"],
                name="tracking_shipment_event_time_idx",
            ),
            # serves: "all events at port Y today" — port authority dashboards
            BTreeIndex(
                fields=["port_id", "event_time"],
                name="tracking_port_event_time_idx",
            ),
            # serves: "all events of type DEPARTURE today" — schedule reporting
            BTreeIndex(
                fields=["event_type", "event_time"],
                name="tracking_event_type_time_idx",
            ),
            # serves: partial — ops exception queue; ~1-2% of rows, huge selectivity gain
            BTreeIndex(
                fields=["shipment_id", "event_time"],
                condition=Q(is_exception=True) & Q(exception_resolved=False),
                name="tracking_unresolved_exc_idx",
            ),
            # serves: append-only bulk scans on event_time for analytics pipelines
            BrinIndex(
                fields=["event_time"],
                name="tracking_event_time_brin_idx",
                pages_per_range=128,
            ),
            BrinIndex(
                fields=["recorded_at"],
                name="tracking_recorded_at_brin_idx",
                pages_per_range=128,
            ),
            # serves: JSONB containment on raw EDI payload for reprocessing queries
            GinIndex(
                fields=["raw_payload"],
                name="tracking_raw_payload_gin_idx",
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(event_type__in=[c[0] for c in TrackingEventType.choices]),
                name="tracking_event_type_valid",
            ),
            # Resolved exceptions must be exceptions first
            CheckConstraint(
                condition=~Q(exception_resolved=True) | Q(is_exception=True),
                name="tracking_resolved_requires_exception",
            ),
        ]
 
    def __str__(self) -> str:
        return f"{self.get_event_type_display()} — {self.shipment_id} @ {self.event_time:%Y-%m-%d %H:%M}"
 
    def __repr__(self) -> str:
        return (
            f"<TrackingEvent id={self.id} shipment={self.shipment_id} "
            f"type={self.event_type} time={self.event_time.isoformat()}>"
        )
 
 
# ── PORT CALL ─────────────────────────────────────────────────────────────────
 
 
class PortCall(TimestampedModel):
    """
    A vessel/aircraft's scheduled or actual visit to a port on a specific route.
    Links routes to physical port visits; used for schedule publishing and delay analytics.
 
    Cardinality: 1 Route → many PortCalls | 1 Port → many PortCalls
    """
 
    route: models.ForeignKey = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name="port_calls",
        db_column="route_id",
    )
    port: models.ForeignKey = models.ForeignKey(
        Port,
        on_delete=models.PROTECT,
        related_name="port_calls",
        db_column="port_id",
    )
    call_sequence: models.PositiveSmallIntegerField = models.PositiveSmallIntegerField(
        db_column="call_sequence",
        help_text="Order of this port in the route itinerary (1-based)",
    )
    vessel_name: models.CharField = models.CharField(
        max_length=128,
        blank=True,
        db_column="vessel_name",
    )
    voyage_number: models.CharField = models.CharField(
        max_length=32,
        blank=True,
        db_column="voyage_number",
    )
    scheduled_arrival: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        db_column="scheduled_arrival",
    )
    actual_arrival: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        db_column="actual_arrival",
    )
    scheduled_departure: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        db_column="scheduled_departure",
    )
    actual_departure: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        db_column="actual_departure",
    )
    # Delay in hours (positive = late, negative = early)
    arrival_delay_hours: models.DecimalField = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        db_column="arrival_delay_hours",
    )
 
    class Meta:
        db_table = "port_calls"
        verbose_name = "Port Call"
        verbose_name_plural = "Port Calls"
        ordering = ["route_id", "call_sequence"]
        indexes = [
            # serves: route itinerary display — ordered port call sequence
            BTreeIndex(
                fields=["route_id", "call_sequence"],
                name="portcall_route_sequence_idx",
            ),
            # serves: "all port calls at port X this week" — port congestion analytics
            BTreeIndex(
                fields=["port_id", "scheduled_arrival"],
                name="portcall_port_sched_arr_idx",
            ),
            # serves: vessel schedule lookup by name + voyage
            BTreeIndex(
                fields=["vessel_name", "voyage_number"],
                name="portcall_vessel_voyage_idx",
            ),
            # serves: delay analysis queries — "port calls with arrival delay > 24h"
            BTreeIndex(
                fields=["arrival_delay_hours"],
                condition=Q(arrival_delay_hours__isnull=False),
                name="portcall_delay_hours_partial_idx",
            ),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(call_sequence__gte=1),
                name="portcall_call_sequence_positive",
            ),
            CheckConstraint(
                condition=Q(scheduled_departure__isnull=True)
                | Q(scheduled_arrival__isnull=True)
                | Q(scheduled_departure__gte=models.F("scheduled_arrival")),
                name="portcall_scheduled_departure_after_arrival",
            ),
            CheckConstraint(
                condition=Q(actual_departure__isnull=True)
                | Q(actual_arrival__isnull=True)
                | Q(actual_departure__gte=models.F("actual_arrival")),
                name="portcall_actual_departure_after_arrival",
            ),
            # Each route can only have one port at each sequence position
            UniqueConstraint(
                fields=["route_id", "call_sequence"],
                name="portcall_route_sequence_unique",
            ),
        ]
 
    def __str__(self) -> str:
        return f"PortCall #{self.call_sequence} — {self.port_id} on Route {self.route_id}"
 
    def __repr__(self) -> str:
        return (
            f"<PortCall id={self.id} route={self.route_id} "
            f"port={self.port_id} seq={self.call_sequence}>"
        )
 
 
# ── ANALYTICS SNAPSHOT ────────────────────────────────────────────────────────
 
 
class AnalyticsSnapshot(TimestampedModel):
    """
    Pre-aggregated analytics fact record. Populated by scheduled jobs or
    materialized view refresh (Phase 4). Stored here for API-layer read
    performance without hitting OLTP tables at query time.
 
    Cardinality: many snapshots per (entity, snapshot_date, metric_key)
    This is the bridge between Phase 1 schema and Phase 4 analytics engine.
    """
 
    class EntityType(models.TextChoices):
        CARRIER = "CARRIER", "Carrier"
        ROUTE = "ROUTE", "Route"
        PORT = "PORT", "Port"
        SHIPPER = "SHIPPER", "Shipper"
        CONSIGNEE = "CONSIGNEE", "Consignee"
        GLOBAL = "GLOBAL", "Global"
 
    class Granularity(models.TextChoices):
        DAILY = "DAILY", "Daily"
        WEEKLY = "WEEKLY", "Weekly"
        MONTHLY = "MONTHLY", "Monthly"
 
    entity_type: models.CharField = models.CharField(
        max_length=12,
        choices=EntityType.choices,
        db_column="entity_type",
    )
    entity_id: models.UUIDField = models.UUIDField(
        null=True,
        blank=True,
        db_column="entity_id",
        help_text="FK to the entity being measured; NULL for GLOBAL snapshots",
    )
    snapshot_date: models.DateField = models.DateField(db_column="snapshot_date")
    granularity: models.CharField = models.CharField(
        max_length=8,
        choices=Granularity.choices,
        db_column="granularity",
    )
    metric_key: models.CharField = models.CharField(
        max_length=128,
        db_column="metric_key",
        help_text="Namespaced metric e.g. shipments.in_transit.count, delay.avg_hours",
    )
    metric_value: models.DecimalField = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        db_column="metric_value",
    )
    dimensions: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        db_column="dimensions",
        help_text="Breakdown dimensions e.g. {carrier_mode: SEA, origin_country: CN}",
    )
 
    class Meta:
        db_table = "analytics_snapshots"
        verbose_name = "Analytics Snapshot"
        verbose_name_plural = "Analytics Snapshots"
        ordering = ["-snapshot_date", "entity_type", "metric_key"]
        indexes = [
            # serves: "metric X for entity Y over date range" — dashboard time series
            BTreeIndex(
                fields=["entity_type", "entity_id", "metric_key", "snapshot_date"],
                name="snapshot_entity_metric_date_idx",
            ),
            # serves: "all metrics for date D at granularity G" — nightly report generation
            BTreeIndex(
                fields=["snapshot_date", "granularity", "entity_type"],
                name="snapshot_date_granularity_idx",
            ),
            # serves: JSONB dimension slice queries — "SEA mode metrics by origin country"
            GinIndex(
                fields=["dimensions"],
                name="snapshot_dimensions_gin_idx",
            ),
        ]
        constraints = [
            # One value per entity/metric/date/granularity combination (upsert target)
            UniqueConstraint(
                fields=["entity_type", "entity_id", "metric_key", "snapshot_date", "granularity"],
                name="snapshot_entity_metric_date_granularity_unique",
            ),
            CheckConstraint(
                condition=Q(entity_type__in=["CARRIER","ROUTE","PORT","SHIPPER","CONSIGNEE","GLOBAL"]),
                name="snapshot_entity_type_valid",
            ),
            CheckConstraint(
                condition=Q(granularity__in=["DAILY","WEEKLY","MONTHLY"]),
                name="snapshot_granularity_valid",
            ),
        ]
 
    def __str__(self) -> str:
        return f"Snapshot [{self.entity_type}/{self.entity_id}] {self.metric_key} @ {self.snapshot_date}"
 
    def __repr__(self) -> str:
        return (
            f"<AnalyticsSnapshot id={self.id} entity_type={self.entity_type} "
            f"metric={self.metric_key!r} date={self.snapshot_date}>"
        )
 