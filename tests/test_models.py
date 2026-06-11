"""
tests/test_models.py

Model constraint tests. Every test triggers an actual PostgreSQL constraint.
These tests document the data integrity guarantees of the schema.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from tests.factories import (
    CargoFactory,
    CarrierFactory,
    CompanyFactory,
    PortFactory,
    RouteFactory,
    ShipmentFactory,
    TrackingEventFactory,
)


@pytest.mark.django_db
class TestCargoConstraints:

    def test_gross_weight_must_be_positive(self):
        """
        PostgreSQL CHECK constraint: cargo_gross_weight_kg_positive.
        Zero or negative weight is physically impossible — reject at DB level.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CargoFactory(gross_weight_kg=Decimal("0"))

    def test_net_weight_cannot_exceed_gross(self):
        """
        PostgreSQL CHECK: cargo_net_weight_lte_gross.
        Net weight above gross weight is a data entry error that would corrupt
        weight-based analytics and freight calculations.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CargoFactory(
                    gross_weight_kg=Decimal("1000.000"),
                    net_weight_kg=Decimal("1001.000"),
                )

    def test_volume_must_be_positive_when_set(self):
        """
        PostgreSQL CHECK: cargo_volume_cbm_positive.
        Zero or negative volume makes LCL rate calculation impossible.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CargoFactory(volume_cbm=Decimal("0.0000"))

    def test_hazmat_requires_un_number(self):
        """
        PostgreSQL CHECK: cargo_hazmat_requires_un_number.
        Hazardous cargo without a UN number cannot be legally transported.
        The DB enforces this — application layer cannot be bypassed.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CargoFactory(is_hazmat=True, un_number="")

    def test_un_number_must_be_4_digits(self):
        """
        PostgreSQL CHECK: cargo_un_number_format.
        UN numbers are exactly 4 digits by international standard.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CargoFactory(is_hazmat=True, un_number="123")  # only 3 digits

    def test_temperature_range_must_be_ordered(self):
        """
        PostgreSQL CHECK: cargo_temperature_range_ordered.
        A reefer setpoint where min > max is physically meaningless.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CargoFactory(
                    temperature_min_c=Decimal("5.00"),
                    temperature_max_c=Decimal("-5.00"),
                )

    def test_package_count_must_be_positive(self):
        """
        PostgreSQL CHECK: cargo_package_count_positive.
        Zero packages = empty shipment = invalid record.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CargoFactory(package_count=0)

    def test_valid_cargo_saves_successfully(self):
        """
        Baseline: a valid cargo record saves without error.
        """
        cargo = CargoFactory(
            gross_weight_kg=Decimal("1000.000"),
            net_weight_kg=Decimal("900.000"),
            volume_cbm=Decimal("20.0000"),
            is_hazmat=False,
            un_number="",
        )
        assert cargo.pk is not None


@pytest.mark.django_db
class TestShipmentConstraints:

    def test_arrival_must_be_after_departure(self):
        """
        PostgreSQL CHECK: shipment_arrival_after_departure.
        Arrival before departure is physically impossible — rejects bad ETAs
        from EDI systems that swap date fields.
        """
        import datetime
        from django.utils import timezone

        departure = timezone.now()
        arrival = departure - datetime.timedelta(days=1)  # before departure

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ShipmentFactory(
                    departure_date=departure,
                    estimated_arrival=arrival,
                )

    def test_declared_value_cannot_be_negative(self):
        """
        PostgreSQL CHECK: shipment_declared_value_non_negative.
        Negative declared value would corrupt customs declarations and insurance.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ShipmentFactory(declared_value=Decimal("-1.00"))

    def test_currency_code_must_be_3_uppercase_letters(self):
        """
        PostgreSQL CHECK: shipment_currency_code_iso4217.
        Non-ISO currency codes cause downstream financial calculation errors.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ShipmentFactory(currency_code="us")  # lowercase — invalid

    def test_bol_unique_when_nonempty(self):
        """
        PostgreSQL UniqueConstraint: shipment_bol_unique_nonempty (WHERE bill_of_lading != '').
        Duplicate B/L numbers cause legal and financial reconciliation failures.
        Empty B/L (pre-booking) is allowed to be duplicated.
        """
        bol = "BOL-UNIQUE-TEST-001"
        ShipmentFactory(bill_of_lading=bol)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ShipmentFactory(bill_of_lading=bol)

    def test_empty_bol_can_be_duplicated(self):
        """
        Sparse unique constraint: empty B/L is allowed multiple times.
        Shipments are booked before B/L is issued.
        """
        s1 = ShipmentFactory(bill_of_lading="")
        s2 = ShipmentFactory(bill_of_lading="")
        assert s1.pk != s2.pk  # both saved successfully

    def test_tracking_number_must_be_unique(self):
        """
        Unique field on tracking_number.
        Duplicate tracking numbers make customer tracking impossible.
        """
        tn = "TRK-DUPLICATE-001"
        ShipmentFactory(tracking_number=tn)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ShipmentFactory(tracking_number=tn)


@pytest.mark.django_db
class TestCarrierConstraints:

    def test_imo_number_must_be_7_digits(self):
        """
        PostgreSQL CHECK: carrier_imo_number_format.
        IMO numbers are exactly 7 digits by international maritime standard.
        Wrong format breaks vessel tracking integrations.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                CarrierFactory(imo_number="12345")  # only 5 digits

    def test_empty_imo_is_allowed(self):
        """
        Non-sea carriers (AIR, ROAD) have no IMO number — empty string is valid.
        """
        from apps.logistics.models import CarrierMode
        carrier = CarrierFactory(mode=CarrierMode.AIR, imo_number="")
        assert carrier.pk is not None


@pytest.mark.django_db
class TestPortConstraints:

    def test_latitude_must_be_in_valid_range(self):
        """
        PostgreSQL CHECK: port_latitude_valid_range.
        Latitude outside -90 to 90 is physically impossible.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PortFactory(un_locode="ZZZZZ", latitude=Decimal("91.000000"))

    def test_longitude_must_be_in_valid_range(self):
        """
        PostgreSQL CHECK: port_longitude_valid_range.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PortFactory(un_locode="ZZZZY", longitude=Decimal("181.000000"))

    def test_country_code_must_be_2_uppercase(self):
        with pytest.raises((IntegrityError, Exception)):
            with transaction.atomic():
                PortFactory(un_locode="ZZZZX", country_code="usa")


@pytest.mark.django_db
class TestTrackingEventConstraints:

    def test_resolved_exception_requires_is_exception_true(self):
        """
        PostgreSQL CHECK: tracking_resolved_requires_exception.
        An event cannot be 'exception resolved' if it was never an exception.
        This constraint prevents data corruption in the exception management workflow.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TrackingEventFactory(
                    is_exception=False,
                    exception_resolved=True,
                )

    def test_exception_event_can_be_resolved(self):
        """
        A genuine exception event can be marked as resolved — valid state.
        """
        event = TrackingEventFactory(is_exception=True, exception_resolved=True)
        assert event.pk is not None


@pytest.mark.django_db
class TestRouteConstraints:

    def test_transit_days_max_must_gte_min(self):
        """
        PostgreSQL CHECK: route_transit_days_max_gte_min.
        max < min is logically impossible and would break ETA calculations.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RouteFactory(transit_days_min=20, transit_days_max=10)

    def test_transit_days_min_must_be_positive(self):
        """
        PostgreSQL CHECK: route_transit_days_min_positive.
        Zero-day transit is physically impossible for sea freight.
        """
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RouteFactory(transit_days_min=0)