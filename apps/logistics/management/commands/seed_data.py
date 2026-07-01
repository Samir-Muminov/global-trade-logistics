"""
apps/logistics/management/commands/seed_data.py

Generates realistic dev/demo data for the Global Trade & Logistics platform.
Idempotent: checks for existing data before creating. Safe to run multiple times.

Usage:
    python manage.py seed_data
    python manage.py seed_data --shipments 500
    python manage.py seed_data --reset
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.logistics.models import (
    Cargo,
    CargoType,
    Carrier,
    CarrierMode,
    Company,
    CompanyType,
    Port,
    PortType,
    Route,
    RouteStatus,
    Shipment,
    ShipmentStatus,
    TrackingEvent,
    TrackingEventType,
)

# ── Realistic reference data ───────────────────────────────────────────────────

PORTS = [
    ("CNSHA", "Shanghai", "CN", "SEA", Decimal("31.230416"), Decimal("121.473701"), "Asia/Shanghai"),
    ("USLAX", "Los Angeles", "US", "SEA", Decimal("33.739500"), Decimal("-118.261500"), "America/Los_Angeles"),
    ("SGSIN", "Singapore", "SG", "SEA", Decimal("1.289700"), Decimal("103.850100"), "Asia/Singapore"),
    ("DEHAM", "Hamburg", "DE", "SEA", Decimal("53.575300"), Decimal("9.989500"), "Europe/Berlin"),
    ("NLRTM", "Rotterdam", "NL", "SEA", Decimal("51.920900"), Decimal("4.460300"), "Europe/Amsterdam"),
    ("AEJEA", "Jebel Ali", "AE", "SEA", Decimal("24.996400"), Decimal("55.060300"), "Asia/Dubai"),
    ("GBFXT", "Felixstowe", "GB", "SEA", Decimal("51.963100"), Decimal("1.351200"), "Europe/London"),
    ("KRPUS", "Busan", "KR", "SEA", Decimal("35.096200"), Decimal("129.040600"), "Asia/Seoul"),
    ("JPTYO", "Tokyo", "JP", "AIR", Decimal("35.652832"), Decimal("139.839478"), "Asia/Tokyo"),
    ("USJFK", "New York JFK", "US", "AIR", Decimal("40.641300"), Decimal("-73.778100"), "America/New_York"),
]

COMPANIES = [
    ("Sino Pacific Trading Co.", "SHIPPER", "CN", "CN123456789"),
    ("Trans-Atlantic Imports GmbH", "CONSIGNEE", "DE", "DE987654321"),
    ("Nordic Freight Forwarders AB", "FF", "SE", "SE112233445"),
    ("Gulf Logistics LLC", "SHIPPER", "AE", "AE998877665"),
    ("Pacific Rim Exports Ltd", "CONSIGNEE", "GB", "GB556677889"),
    ("Ameritrade Shipping Corp", "SHIPPER", "US", "US334455667"),
    ("Asia Gateway Co.", "CONSIGNEE", "SG", "SG778899001"),
    ("Euro Customs Brokers BV", "CB", "NL", "NL445566778"),
]

CARRIERS = [
    ("MSCO", "MSC Mediterranean Shipping", "SEA", "9321483"),
    ("MAEU", "Maersk Line", "SEA", "9450648"),
    ("COSU", "COSCO Shipping", "SEA", "9227838"),
    ("EVER", "Evergreen Marine", "SEA", "9112742"),
    ("HALU", "Hapag-Lloyd", "SEA", "9231743"),
]

HS_CODES_POOL = [
    ["8471.30", "8473.30"],
    ["6109.10", "6110.20"],
    ["8544.42", "8544.49"],
    ["9403.20", "9403.60"],
    ["3926.90"],
    ["7318.15", "7318.16"],
    ["8708.29", "8708.99"],
]

INCOTERMS_POOL = ["FOB", "CIF", "CFR", "DAP", "EXW", "DDP"]


class Command(BaseCommand):
    help = (
        "Seeds the database with realistic demo data. "
        "Idempotent — safe to run multiple times. "
        "Use --reset to wipe existing seed data first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--shipments",
            type=int,
            default=200,
            help="Number of shipments to create (default: 200).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all existing seed data before creating new data.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write("── Resetting seed data ──")
            self._reset()

        self.stdout.write("── Seeding ports ──")
        ports = self._seed_ports()

        self.stdout.write("── Seeding companies ──")
        companies = self._seed_companies()

        self.stdout.write("── Seeding carriers ──")
        carriers = self._seed_carriers(companies)

        self.stdout.write("── Seeding routes ──")
        routes = self._seed_routes(carriers, ports)

        self.stdout.write("── Seeding shipments ──")
        shipment_count = options["shipments"]
        shipments = self._seed_shipments(companies, carriers, routes, ports, shipment_count)

        self.stdout.write("── Seeding cargo ──")
        self._seed_cargo(shipments)

        self.stdout.write("── Seeding tracking events ──")
        self._seed_tracking_events(shipments, ports)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n── Seed complete: {len(ports)} ports, {len(companies)} companies, "
                f"{len(carriers)} carriers, {len(routes)} routes, "
                f"{len(shipments)} shipments ──\n"
            )
        )

    def _reset(self):
        TrackingEvent.objects.filter(source_system__startswith="SEED").delete()
        Cargo.objects.filter(description__startswith="[SEED]").delete()
        Shipment.objects.filter(tags__contains=["seed"]).delete()
        self.stdout.write(self.style.WARNING("   ✓ Existing seed data cleared"))

    def _seed_ports(self) -> list:
        ports = []
        for locode, name, country, ptype, lat, lon, tz in PORTS:
            port, created = Port.objects.get_or_create(
                un_locode=locode,
                defaults={
                    "port_name": name,
                    "country_code": country,
                    "port_type": ptype,
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": tz,
                    "is_active": True,
                },
            )
            ports.append(port)
        self.stdout.write(f"   ✓ {len(ports)} ports ready")
        return ports

    def _seed_companies(self) -> list:
        companies = []
        for i, (name, ctype, country, tax_id) in enumerate(COMPANIES):
            company, _ = Company.objects.get_or_create(
                tax_id=tax_id,
                defaults={
                    "legal_name": name,
                    "company_type": ctype,
                    "country_code": country,
                    "is_active": True,
                },
            )
            companies.append(company)
        self.stdout.write(f"   ✓ {len(companies)} companies ready")
        return companies

    def _seed_carriers(self, companies: list) -> list:
        carriers = []
        carrier_company = companies[0]
        for code, name, mode, imo in CARRIERS:
            carrier, _ = Carrier.objects.get_or_create(
                carrier_code=code,
                defaults={
                    "company": carrier_company,
                    "carrier_name": name,
                    "mode": mode,
                    "imo_number": imo,
                    "is_active": True,
                    "hub_ports": ["CNSHA", "SGSIN"],
                    "service_metadata": {
                        "webhook_secret": f"secret-{code.lower()}-webhook-key-2025",
                        "alliance": "2M" if code in ("MSCO", "MAEU") else "THE",
                    },
                },
            )
            carriers.append(carrier)
        self.stdout.write(f"   ✓ {len(carriers)} carriers ready")
        return carriers

    def _seed_routes(self, carriers: list, ports: list) -> list:
        routes = []
        sea_ports = [p for p in ports if p.port_type == "SEA"]

        for i, carrier in enumerate(carriers):
            for j in range(4):
                origin = sea_ports[j % len(sea_ports)]
                destination = sea_ports[(j + 2) % len(sea_ports)]
                if origin == destination:
                    destination = sea_ports[(j + 3) % len(sea_ports)]

                route_code = f"{origin.un_locode}-{destination.un_locode}-{carrier.carrier_code}"
                route, _ = Route.objects.get_or_create(
                    route_code=route_code,
                    defaults={
                        "carrier": carrier,
                        "origin_port": origin,
                        "destination_port": destination,
                        "status": RouteStatus.ACTIVE,
                        "transit_days_min": random.randint(14, 18),
                        "transit_days_max": random.randint(19, 28),
                        "weekly_frequency": random.randint(1, 3),
                        "effective_from": timezone.now().date() - timedelta(days=365),
                        "effective_until": None,
                    },
                )
                routes.append(route)

        self.stdout.write(f"   ✓ {len(routes)} routes ready")
        return routes

    def _seed_shipments(
        self,
        companies: list,
        carriers: list,
        routes: list,
        ports: list,
        count: int,
    ) -> list:
        shippers = [c for c in companies if c.company_type in ("SHIPPER",)]
        consignees = [c for c in companies if c.company_type in ("CONSIGNEE",)]

        if not shippers:
            shippers = companies[:3]
        if not consignees:
            consignees = companies[3:]

        now = timezone.now()
        shipments = []
        existing_tns = set(
            Shipment.objects.filter(tags__contains=["seed"])
            .values_list("tracking_number", flat=True)
        )

        status_weights = [
            (ShipmentStatus.DELIVERED, 40),
            (ShipmentStatus.IN_TRANSIT, 30),
            (ShipmentStatus.BOOKED, 15),
            (ShipmentStatus.AT_PORT, 8),
            (ShipmentStatus.EXCEPTION, 4),
            (ShipmentStatus.CANCELLED, 3),
        ]
        statuses = [s for s, w in status_weights for _ in range(w)]

        batch = []
        for i in range(count):
            route = random.choice(routes)
            shipper = random.choice(shippers)
            consignee = random.choice(consignees)
            ship_status = random.choice(statuses)

            # Departure in the past (-180 to -3 days)
            days_ago = random.randint(3, 180)
            departure = now - timedelta(days=days_ago)
            transit_days = random.randint(
                route.transit_days_min, route.transit_days_max
            )
            eta = departure + timedelta(days=transit_days)
            actual_arrival = None
            if ship_status == ShipmentStatus.DELIVERED:
                delay_days = random.randint(-2, 5)
                actual_arrival = eta + timedelta(days=delay_days)

            tn = f"GT-SEED{i:06d}"
            if tn in existing_tns:
                continue

            batch.append(Shipment(
                shipper=shipper,
                consignee=consignee,
                carrier=route.carrier,
                route=route,
                origin_port=route.origin_port,
                destination_port=route.destination_port,
                tracking_number=tn,
                status=ship_status,
                booking_date=departure.date() - timedelta(days=random.randint(3, 14)),
                departure_date=departure,
                estimated_arrival=eta,
                actual_arrival=actual_arrival,
                declared_value=Decimal(str(random.randint(5000, 500000))),
                freight_cost=Decimal(str(random.randint(800, 25000))),
                currency_code="USD",
                incoterms=random.choice(INCOTERMS_POOL),
                hs_codes=random.choice(HS_CODES_POOL),
                tags=["seed"],
                custom_attributes={"seed": True},
            ))

        created = Shipment.objects.bulk_create(batch, ignore_conflicts=True)
        shipments = list(
            Shipment.objects.filter(tags__contains=["seed"])
        )
        self.stdout.write(f"   ✓ {len(shipments)} shipments ready")
        return shipments

    def _seed_cargo(self, shipments: list) -> None:
        cargo_batch = []
        for shipment in random.sample(shipments, min(len(shipments), len(shipments))):
            num_items = random.randint(1, 3)
            for _ in range(num_items):
                gross = Decimal(str(random.randint(500, 25000)))
                net = gross * Decimal("0.92")
                cargo_batch.append(Cargo(
                    shipment=shipment,
                    cargo_type=random.choice([
                        CargoType.CONTAINER,
                        CargoType.GENERAL,
                        CargoType.BREAKBULK,
                    ]),
                    description=f"[SEED] {random.choice(['Electronics', 'Textiles', 'Machinery', 'Chemicals', 'Auto Parts'])}",
                    container_number=f"SEED{random.randint(1000000, 9999999)}",
                    gross_weight_kg=gross,
                    net_weight_kg=net,
                    volume_cbm=Decimal(str(random.randint(20, 67))),
                    package_count=random.randint(10, 500),
                    package_type="CT",
                    is_hazmat=False,
                ))

        Cargo.objects.bulk_create(cargo_batch, ignore_conflicts=True)
        self.stdout.write(f"   ✓ {len(cargo_batch)} cargo items ready")

    def _seed_tracking_events(self, shipments: list, ports: list) -> None:
        sea_ports = [p for p in ports if p.port_type == "SEA"]
        events_batch = []

        for shipment in shipments:
            if shipment.status in (ShipmentStatus.BOOKED,):
                events_batch.append(TrackingEvent(
                    shipment=shipment,
                    event_type=TrackingEventType.DOCUMENT_RECEIVED,
                    event_time=shipment.departure_date - timedelta(days=1),
                    description="Booking confirmed.",
                    source_system="SEED",
                    raw_payload={},
                ))

            elif shipment.status in (
                ShipmentStatus.IN_TRANSIT,
                ShipmentStatus.AT_PORT,
                ShipmentStatus.DELIVERED,
                ShipmentStatus.EXCEPTION,
            ):
                events_batch.append(TrackingEvent(
                    shipment=shipment,
                    event_type=TrackingEventType.DEPARTURE,
                    event_time=shipment.departure_date,
                    port=shipment.origin_port,
                    description="Vessel departed origin port.",
                    source_system="SEED",
                    raw_payload={},
                ))

                if shipment.status in (ShipmentStatus.AT_PORT, ShipmentStatus.DELIVERED):
                    arrival_time = shipment.actual_arrival or shipment.estimated_arrival
                    events_batch.append(TrackingEvent(
                        shipment=shipment,
                        event_type=TrackingEventType.ARRIVAL,
                        event_time=arrival_time,
                        port=shipment.destination_port,
                        description="Vessel arrived at destination port.",
                        source_system="SEED",
                        raw_payload={},
                    ))

                if shipment.status == ShipmentStatus.DELIVERED:
                    events_batch.append(TrackingEvent(
                        shipment=shipment,
                        event_type=TrackingEventType.DELIVERED,
                        event_time=shipment.actual_arrival,
                        description="Shipment delivered to consignee.",
                        source_system="SEED",
                        raw_payload={},
                    ))

                if shipment.status == ShipmentStatus.EXCEPTION:
                    events_batch.append(TrackingEvent(
                        shipment=shipment,
                        event_type=TrackingEventType.EXCEPTION,
                        event_time=shipment.departure_date + timedelta(days=3),
                        description="[SEED] Exception: customs documentation incomplete.",
                        is_exception=True,
                        exception_resolved=False,
                        source_system="SEED",
                        raw_payload={},
                    ))

        TrackingEvent.objects.bulk_create(events_batch, ignore_conflicts=True)
        self.stdout.write(f"   ✓ {len(events_batch)} tracking events ready")