"""
tests/conftest.py

pytest-django fixtures shared across all test modules.
"""

from __future__ import annotations

import pytest
from django.db import connection
from rest_framework.test import APIClient

from tests.factories import (
    CargoFactory,
    CarrierFactory,
    CompanyFactory,
    CustomUserFactory,
    PortFactory,
    RouteFactory,
    ShipmentFactory,
    TrackingEventFactory,
)
from apps.logistics.models import ShipmentStatus

from django.test.utils import override_settings

@pytest.fixture(autouse=True)
def disable_cache(settings):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
    }

@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """
    Ensures required PostgreSQL extensions are active in the test database.
    These are created by 0001_initial.py RunSQL — but test DB may be created
    fresh without running migrations if using --no-migrations flag.
    """
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gin;")
            cursor.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def staff_user(db):
    """Internal staff user — no company, no carrier, is_staff=True."""
    return CustomUserFactory(
        user_type="STAFF",
        company=None,
        carrier=None,
        is_staff=True,
        is_email_verified=True,
    )


@pytest.fixture
def shipper_user(db):
    """Shipper user — has company, no carrier."""
    company = CompanyFactory()
    return CustomUserFactory(
        user_type="SHIPPER",
        company=company,
        carrier=None,
        is_email_verified=True,
    )


@pytest.fixture
def consignee_user(db):
    """Consignee user — has company, no carrier."""
    company = CompanyFactory()
    return CustomUserFactory(
        user_type="CONSIGNEE",
        company=company,
        carrier=None,
        is_email_verified=True,
    )


@pytest.fixture
def carrier_user(db):
    """Carrier staff user — has carrier, no company."""
    carrier = CarrierFactory()
    return CustomUserFactory(
        user_type="CARRIER_STAFF",
        company=None,
        carrier=carrier,
        is_email_verified=True,
    )


@pytest.fixture
def unverified_user(db):
    """User who has not verified their email."""
    return CustomUserFactory(is_email_verified=False)


@pytest.fixture
def shipment(db, shipper_user):
    """
    Standard in-transit shipment owned by shipper_user's company.
    """
    return ShipmentFactory(
        shipper=shipper_user.company,
        status=ShipmentStatus.IN_TRANSIT,
    )


@pytest.fixture
def shipment_delayed(db, shipper_user):
    """
    Shipment past ETA, not delivered — should appear in .delayed() queryset.
    """
    import datetime
    from django.utils import timezone
    return ShipmentFactory(
        shipper=shipper_user.company,
        status=ShipmentStatus.IN_TRANSIT,
        departure_date=timezone.now() - datetime.timedelta(days=30),
        estimated_arrival=timezone.now() - datetime.timedelta(days=5),
        actual_arrival=None,
    )


@pytest.fixture
def shipment_with_cargo(db, shipment):
    """Shipment with 3 cargo items."""
    for _ in range(3):
        CargoFactory(shipment=shipment)
    return shipment


@pytest.fixture
def shipment_with_events(db, shipment):
    """Shipment with departure and arrival tracking events."""
    from apps.logistics.models import TrackingEventType
    TrackingEventFactory(
        shipment=shipment,
        event_type=TrackingEventType.DEPARTURE,
    )
    TrackingEventFactory(
        shipment=shipment,
        event_type=TrackingEventType.TRANSSHIPMENT,
    )
    return shipment


@pytest.fixture
def other_company_shipment(db):
    """Shipment owned by a completely different company — for IDOR tests."""
    other_company = CompanyFactory()
    return ShipmentFactory(shipper=other_company)