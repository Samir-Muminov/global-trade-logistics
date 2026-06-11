"""
tests/factories.py

factory-boy factories for all models.
Realistic data — not test123 garbage.
"""

from __future__ import annotations

import random
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from factory.django import DjangoModelFactory

from apps.logistics.models import (
    Cargo,
    Carrier,
    CarrierMode,
    CargoType,
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

User = get_user_model()

# Realistic UN/LOCODE samples
REAL_PORTS = [
    ("CNSHA", "Shanghai", "CN", "SEA", 31.2304, 121.4737, "Asia/Shanghai"),
    ("USLAX", "Los Angeles", "US", "SEA", 33.7395, -118.2615, "America/Los_Angeles"),
    ("SGSIN", "Singapore", "SG", "SEA", 1.2897, 103.8501, "Asia/Singapore"),
    ("DEHAM", "Hamburg", "DE", "SEA", 53.5753, 9.9895, "Europe/Berlin"),
    ("AEJEA", "Jebel Ali", "AE", "SEA", 24.9964, 55.0603, "Asia/Dubai"),
    ("GBLON", "London Heathrow", "GB", "AIR", 51.4700, -0.4543, "Europe/London"),
    ("USJFK", "New York JFK", "US", "AIR", 40.6413, -73.7781, "America/New_York"),
    ("DEDUS", "Dusseldorf", "DE", "AIR", 51.2895, 6.7668, "Europe/Berlin"),
]


class CompanyFactory(DjangoModelFactory):
    class Meta:
        model = Company

    legal_name = factory.Sequence(lambda n: f"Global Logistics Corp {n} Ltd")
    trade_name = factory.LazyAttribute(lambda o: o.legal_name.replace(" Ltd", ""))
    company_type = factory.Iterator([c[0] for c in CompanyType.choices])
    tax_id = factory.Sequence(lambda n: f"TAX{n:010d}")
    country_code = factory.Iterator(["US", "DE", "CN", "SG", "GB", "AE", "JP", "NL"])
    is_active = True
    metadata = factory.LazyFunction(dict)


class PortFactory(DjangoModelFactory):
    class Meta:
        model = Port
        django_get_or_create = ("un_locode",)

    un_locode = factory.Iterator([p[0] for p in REAL_PORTS])
    port_name = factory.Iterator([p[1] for p in REAL_PORTS])
    country_code = factory.Iterator([p[2] for p in REAL_PORTS])
    port_type = factory.Iterator([p[3] for p in REAL_PORTS])
    latitude = factory.Iterator([Decimal(str(p[4])) for p in REAL_PORTS])
    longitude = factory.Iterator([Decimal(str(p[5])) for p in REAL_PORTS])
    timezone = factory.Iterator([p[6] for p in REAL_PORTS])
    is_active = True


class CarrierFactory(DjangoModelFactory):
    class Meta:
        model = Carrier

    company = factory.SubFactory(CompanyFactory)
    carrier_code = factory.Sequence(lambda n: f"SCR{n:04d}")
    carrier_name = factory.Sequence(lambda n: f"Trans Ocean Shipping Line {n}")
    mode = CarrierMode.SEA
    imo_number = factory.Sequence(lambda n: f"{9000000 + n:07d}")
    is_active = True
    hub_ports = factory.LazyFunction(lambda: ["CNSHA", "SGSIN"])
    service_metadata = factory.LazyFunction(dict)


class RouteFactory(DjangoModelFactory):
    class Meta:
        model = Route

    carrier = factory.SubFactory(CarrierFactory)
    origin_port = factory.SubFactory(PortFactory)
    destination_port = factory.SubFactory(PortFactory)
    route_code = factory.Sequence(lambda n: f"RT-{n:06d}")
    status = RouteStatus.ACTIVE
    transit_days_min = 14
    transit_days_max = 21
    transshipment_ports = factory.LazyFunction(list)
    weekly_frequency = 2
    effective_from = factory.LazyFunction(
        lambda: __import__("django.utils.timezone", fromlist=["now"]).now().date()
    )
    effective_until = None


class ShipmentFactory(DjangoModelFactory):
    class Meta:
        model = Shipment

    shipper = factory.SubFactory(CompanyFactory)
    consignee = factory.SubFactory(CompanyFactory)
    carrier = factory.SubFactory(CarrierFactory)
    route = factory.SubFactory(RouteFactory)
    origin_port = factory.SubFactory(PortFactory)
    destination_port = factory.SubFactory(PortFactory)
    tracking_number = factory.Sequence(lambda n: f"TRK{n:012d}")
    bill_of_lading = factory.Sequence(lambda n: f"BOL{n:010d}")
    status = ShipmentStatus.IN_TRANSIT
    departure_date = factory.LazyFunction(
        lambda: __import__("django.utils.timezone", fromlist=["now"]).now()
        - __import__("datetime").timedelta(days=7)
    )
    estimated_arrival = factory.LazyFunction(
        lambda: __import__("django.utils.timezone", fromlist=["now"]).now()
        + __import__("datetime").timedelta(days=14)
    )
    declared_value = factory.LazyFunction(
        lambda: Decimal(str(random.randint(10000, 500000)))
    )
    freight_cost = factory.LazyFunction(
        lambda: Decimal(str(random.randint(1000, 50000)))
    )
    currency_code = "USD"
    incoterms = "FOB"
    hs_codes = factory.LazyFunction(lambda: ["8471.30", "8473.30"])
    tags = factory.LazyFunction(list)
    custom_attributes = factory.LazyFunction(dict)
    notes = ""


class CargoFactory(DjangoModelFactory):
    class Meta:
        model = Cargo

    shipment = factory.SubFactory(ShipmentFactory)
    cargo_type = CargoType.CONTAINER
    description = "Electronic Components — LCD Panels"
    container_number = factory.Sequence(lambda n: f"MSCU{n:07d}")
    gross_weight_kg = factory.LazyFunction(
        lambda: Decimal(str(random.randint(5000, 25000)))
    )
    net_weight_kg = factory.LazyFunction(
        lambda: Decimal(str(random.randint(4000, 20000)))
    )
    volume_cbm = factory.LazyFunction(
        lambda: Decimal(str(random.randint(20, 67)))
    )
    package_count = factory.LazyFunction(lambda: random.randint(10, 500))
    package_type = "CT"
    is_hazmat = False
    un_number = ""
    imdg_class = ""
    custom_attributes = factory.LazyFunction(dict)


class TrackingEventFactory(DjangoModelFactory):
    class Meta:
        model = TrackingEvent

    shipment = factory.SubFactory(ShipmentFactory)
    port = factory.SubFactory(PortFactory)
    event_type = TrackingEventType.DEPARTURE
    event_time = factory.LazyFunction(
        lambda: __import__("django.utils.timezone", fromlist=["now"]).now()
        - __import__("datetime").timedelta(days=5)
    )
    description = "Vessel departed on schedule"
    is_exception = False
    exception_resolved = False
    source_system = "TEST"
    raw_payload = factory.LazyFunction(dict)


class CustomUserFactory(DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@globaltradetest.com")
    first_name = factory.Iterator(["Alice", "Bob", "Carlos", "Diana", "Erik"])
    last_name = factory.Iterator(["Smith", "Johnson", "Lee", "Wang", "Mueller"])
    user_type = "SHIPPER"
    company = factory.SubFactory(CompanyFactory)
    carrier = None
    is_active = True
    is_staff = False
    is_email_verified = True

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        obj.set_password(extracted or "TestPass123!")
        if create:
            obj.save(update_fields=["password"])