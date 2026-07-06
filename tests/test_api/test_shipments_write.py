"""
tests/test_api/test_shipments_write.py

Tests for Phase 5 write API: shipment creation, status transitions,
cargo creation, tracking events, and webhooks.

Every test documents a real-world scenario this code must handle correctly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status

from apps.logistics.models import Cargo, Shipment, ShipmentStatus, TrackingEvent
from tests.factories import (
    CarrierFactory,
    CompanyFactory,
    PortFactory,
    RouteFactory,
    ShipmentFactory,
)


@pytest.fixture
def booking_setup(db):
    """
    Full set of related objects needed to book a shipment:
    active carrier, route belonging to that carrier, matching ports.
    """
    carrier = CarrierFactory(is_active=True)
    origin = PortFactory(un_locode="USLAX")
    destination = PortFactory(un_locode="CNSHA")
    route = RouteFactory(
        carrier=carrier,
        origin_port=origin,
        destination_port=destination,
        status="ACTIVE",
    )
    return {
        "carrier": carrier,
        "route": route,
        "origin": origin,
        "destination": destination,
    }


def _valid_booking_payload(shipper_id, consignee_id, setup, **overrides):
    import datetime
    payload = {
        "shipper_id": str(shipper_id),
        "consignee_id": str(consignee_id),
        "carrier_id": str(setup["carrier"].id),
        "route_id": str(setup["route"].id),
        "origin_port_id": str(setup["origin"].id),
        "destination_port_id": str(setup["destination"].id),
        "departure_date": (
            timezone.now() + datetime.timedelta(days=10)
        ).isoformat(),
        "declared_value": "50000.00",
        "currency_code": "USD",
        "idempotency_key": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestShipmentCreate:

    def test_create_shipment_success(self, api_client, shipper_user, booking_setup):
        """
        Happy path: shipper books a shipment with all valid references.
        Must create both the Shipment and an initial TrackingEvent atomically.
        """
        api_client.force_authenticate(user=shipper_user)
        payload = _valid_booking_payload(
            shipper_user.company_id, CompanyFactory().id, booking_setup
        )

        response = api_client.post(
            "/api/v1/shipments/create/", payload, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["tracking_number"].startswith("GT-")

        shipment = Shipment.objects.get(tracking_number=data["tracking_number"])
        assert shipment.status == ShipmentStatus.DRAFT
        assert TrackingEvent.objects.filter(shipment=shipment).count() == 1

    def test_create_shipment_idempotent(self, api_client, shipper_user, booking_setup):
        """
        Same idempotency_key submitted twice must NOT create two shipments.
        Real-world: client retries after a timeout, network blip, or double-click.
        """
        api_client.force_authenticate(user=shipper_user)
        payload = _valid_booking_payload(
            shipper_user.company_id, CompanyFactory().id, booking_setup
        )

        r1 = api_client.post("/api/v1/shipments/create/", payload, format="json")
        r2 = api_client.post("/api/v1/shipments/create/", payload, format="json")

        assert r1.status_code == status.HTTP_201_CREATED
        assert r2.status_code == status.HTTP_200_OK
        assert r1.json()["tracking_number"] == r2.json()["tracking_number"]
        assert Shipment.objects.filter(
            tracking_number=r1.json()["tracking_number"]
        ).count() == 1

    def test_create_rejects_inactive_carrier(self, api_client, shipper_user, booking_setup):
        """
        Booking with a suspended carrier must be rejected with 400.
        Real-world: prevents shippers from booking with carriers under compliance review.
        """
        booking_setup["carrier"].is_active = False
        booking_setup["carrier"].save()

        api_client.force_authenticate(user=shipper_user)
        payload = _valid_booking_payload(
            shipper_user.company_id, CompanyFactory().id, booking_setup
        )
        response = api_client.post(
            "/api/v1/shipments/create/", payload, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_rejects_route_carrier_mismatch(
        self, api_client, shipper_user, booking_setup
    ):
        """
        Booking where route belongs to a different carrier than specified
        must be rejected — prevents inconsistent carrier/route data.
        """
        other_carrier = CarrierFactory(is_active=True)
        api_client.force_authenticate(user=shipper_user)
        payload = _valid_booking_payload(
            shipper_user.company_id,
            CompanyFactory().id,
            booking_setup,
            carrier_id=str(other_carrier.id),
        )
        response = api_client.post(
            "/api/v1/shipments/create/", payload, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_rejects_past_departure_date(
        self, api_client, shipper_user, booking_setup
    ):
        """
        Departure date in the past must be rejected — prevents
        backdated bookings that would corrupt analytics timelines.
        """
        import datetime
        api_client.force_authenticate(user=shipper_user)
        payload = _valid_booking_payload(
            shipper_user.company_id,
            CompanyFactory().id,
            booking_setup,
            departure_date=(timezone.now() - datetime.timedelta(days=1)).isoformat(),
        )
        response = api_client.post(
            "/api/v1/shipments/create/", payload, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_rejects_wrong_shipper(
        self, api_client, shipper_user, booking_setup
    ):
        """
        A shipper cannot book a shipment on behalf of a different company.
        Real-world: prevents one company from creating fraudulent bookings
        attributed to a competitor.
        """
        other_company = CompanyFactory()
        api_client.force_authenticate(user=shipper_user)
        payload = _valid_booking_payload(
            other_company.id,  # NOT shipper_user's company
            CompanyFactory().id,
            booking_setup,
        )
        response = api_client.post(
            "/api/v1/shipments/create/", payload, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_requires_authentication(self, api_client, booking_setup):
        """Unauthenticated booking attempts must return 401."""
        payload = _valid_booking_payload(
            uuid.uuid4(), uuid.uuid4(), booking_setup
        )
        response = api_client.post(
            "/api/v1/shipments/create/", payload, format="json"
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestShipmentStatusUpdate:

    def test_valid_transition_succeeds(self, api_client, carrier_user):
        """
        BOOKED → IN_TRANSIT is a valid transition.
        Must update status and create a matching TrackingEvent atomically.
        """
        shipment = ShipmentFactory(
            carrier=carrier_user.carrier,
            status=ShipmentStatus.BOOKED,
        )
        api_client.force_authenticate(user=carrier_user)

        response = api_client.patch(
            f"/api/v1/shipments/{shipment.id}/status/",
            {"new_status": "IN_TRANSIT", "note": "Departed on schedule."},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        shipment.refresh_from_db()
        assert shipment.status == ShipmentStatus.IN_TRANSIT
        assert TrackingEvent.objects.filter(
            shipment=shipment, event_type="DEPARTURE"
        ).exists()

    def test_invalid_transition_rejected(self, api_client, carrier_user):
        """
        DELIVERED is terminal — no further transitions allowed.
        Real-world: prevents accidental "un-delivering" a shipment.
        """
        shipment = ShipmentFactory(
            carrier=carrier_user.carrier,
            status=ShipmentStatus.DELIVERED,
        )
        api_client.force_authenticate(user=carrier_user)

        response = api_client.patch(
            f"/api/v1/shipments/{shipment.id}/status/",
            {"new_status": "IN_TRANSIT"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        shipment.refresh_from_db()
        assert shipment.status == ShipmentStatus.DELIVERED  # unchanged

    def test_unrelated_carrier_staff_cannot_update(self, api_client, carrier_user):
        """
        Carrier staff for Carrier A cannot update status of Carrier B's shipment.
        IDOR-style check on the write path.
        """
        other_carrier = CarrierFactory()
        shipment = ShipmentFactory(
            carrier=other_carrier,
            status=ShipmentStatus.BOOKED,
        )
        api_client.force_authenticate(user=carrier_user)

        response = api_client.patch(
            f"/api/v1/shipments/{shipment.id}/status/",
            {"new_status": "IN_TRANSIT"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delivery_sets_actual_arrival(self, api_client, carrier_user):
        """
        Transitioning to DELIVERED must set actual_arrival timestamp.
        Required for on-time delivery rate calculations in analytics.
        """
        shipment = ShipmentFactory(
            carrier=carrier_user.carrier,
            status=ShipmentStatus.AT_PORT,
            actual_arrival=None,
        )
        api_client.force_authenticate(user=carrier_user)

        response = api_client.patch(
            f"/api/v1/shipments/{shipment.id}/status/",
            {"new_status": "DELIVERED"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        shipment.refresh_from_db()
        assert shipment.actual_arrival is not None

    def test_staff_can_update_any_shipment_status(self, api_client, staff_user):
        """Internal staff bypass carrier ownership check."""
        shipment = ShipmentFactory(status=ShipmentStatus.BOOKED)
        api_client.force_authenticate(user=staff_user)

        response = api_client.patch(
            f"/api/v1/shipments/{shipment.id}/status/",
            {"new_status": "IN_TRANSIT"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestCargoCreate:

    def test_create_cargo_success(self, api_client, shipper_user):
        """Shipper adds a valid cargo item to their own shipment."""
        shipment = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.BOOKED,
        )
        api_client.force_authenticate(user=shipper_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/cargo/",
            {
                "cargo_type": "CONTAINER",
                "description": "Electronics",
                "gross_weight_kg": "5000.000",
                "net_weight_kg": "4500.000",
                "package_count": 100,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Cargo.objects.filter(shipment=shipment).count() == 1

    def test_create_cargo_rejects_net_exceeds_gross(self, api_client, shipper_user):
        """
        Serializer-level validation: net weight > gross weight returns 400,
        not a 500 from the DB constraint violation.
        """
        shipment = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.BOOKED,
        )
        api_client.force_authenticate(user=shipper_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/cargo/",
            {
                "cargo_type": "CONTAINER",
                "description": "Electronics",
                "gross_weight_kg": "1000.000",
                "net_weight_kg": "2000.000",
                "package_count": 10,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_cargo_rejects_hazmat_without_un_number(
        self, api_client, shipper_user
    ):
        """Hazmat cargo without UN number must be rejected at serializer level."""
        shipment = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.BOOKED,
        )
        api_client.force_authenticate(user=shipper_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/cargo/",
            {
                "cargo_type": "HAZMAT",
                "description": "Lithium batteries",
                "gross_weight_kg": "500.000",
                "package_count": 5,
                "is_hazmat": True,
                "un_number": "",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_cargo_rejects_on_delivered_shipment(
        self, api_client, shipper_user
    ):
        """
        Cargo cannot be added to a DELIVERED shipment — immutability
        of terminal-status shipments.
        """
        shipment = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.DELIVERED,
        )
        api_client.force_authenticate(user=shipper_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/cargo/",
            {
                "cargo_type": "CONTAINER",
                "description": "Electronics",
                "gross_weight_kg": "5000.000",
                "package_count": 100,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unrelated_user_cannot_add_cargo(self, api_client):
        """A shipper from a different company cannot add cargo to this shipment."""
        shipment = ShipmentFactory()
        other_user = CompanyFactory()
        from tests.factories import CustomUserFactory
        unrelated = CustomUserFactory(company=CompanyFactory())
        api_client.force_authenticate(user=unrelated)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/cargo/",
            {
                "cargo_type": "CONTAINER",
                "description": "Test",
                "gross_weight_kg": "100.000",
                "package_count": 1,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestTrackingEventCreate:

    def test_carrier_staff_can_create_event(self, api_client, carrier_user):
        """Carrier staff creates a valid tracking event for their shipment."""
        shipment = ShipmentFactory(
            carrier=carrier_user.carrier,
            status=ShipmentStatus.IN_TRANSIT,
        )
        api_client.force_authenticate(user=carrier_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/events/",
            {
                "event_type": "ARRIVAL",
                "event_time": timezone.now().isoformat(),
                "description": "Vessel arrived at transshipment port.",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_shipper_cannot_create_event(self, api_client, shipper_user):
        """
        Shippers cannot self-report tracking events — only carrier staff can.
        Prevents tampering with the official tracking timeline.
        """
        shipment = ShipmentFactory(
            shipper=shipper_user.company,
            status=ShipmentStatus.IN_TRANSIT,
        )
        api_client.force_authenticate(user=shipper_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/events/",
            {
                "event_type": "ARRIVAL",
                "event_time": timezone.now().isoformat(),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_exception_requires_description(self, api_client, carrier_user):
        """is_exception=True without exception_description must be rejected."""
        shipment = ShipmentFactory(
            carrier=carrier_user.carrier,
            status=ShipmentStatus.IN_TRANSIT,
        )
        api_client.force_authenticate(user=carrier_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/events/",
            {
                "event_type": "EXCEPTION",
                "event_time": timezone.now().isoformat(),
                "is_exception": True,
                "exception_description": "",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_future_event_time_rejected(self, api_client, carrier_user):
        """
        Events cannot be timestamped in the future — they record facts
        that have already happened.
        """
        import datetime
        shipment = ShipmentFactory(
            carrier=carrier_user.carrier,
            status=ShipmentStatus.IN_TRANSIT,
        )
        api_client.force_authenticate(user=carrier_user)

        response = api_client.post(
            f"/api/v1/shipments/{shipment.id}/events/",
            {
                "event_type": "ARRIVAL",
                "event_time": (
                    timezone.now() + datetime.timedelta(days=1)
                ).isoformat(),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestWebhookReceive:

    def _sign(self, payload_bytes: bytes, secret: str) -> str:
        digest = hmac.new(
            secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        return f"sha256={digest}"

    def test_valid_signature_accepted(self, api_client):
        """
        Webhook with a correct HMAC signature must be accepted and processed,
        even with no JWT token. Matches against a real shipment.
        """
        carrier = CarrierFactory(
            is_active=True,
            service_metadata={"webhook_secret": "test-secret-123"},
        )
        shipment = ShipmentFactory(carrier=carrier)
        body = {
            "webhook_id": "evt_test_001",
            "event_type": "vessel.departed",
            "shipment_reference": shipment.tracking_number,
            "event_timestamp": timezone.now().isoformat(),
            "payload": {},
        }
        body_bytes = json.dumps(body).encode()
        signature = self._sign(body_bytes, "test-secret-123")

        response = api_client.post(
            f"/api/v1/webhooks/carrier/{carrier.carrier_code}/",
            data=body_bytes,
            content_type="application/json",
            HTTP_X_SIGNATURE_SHA256=signature,
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["shipment_matched"] is True

    def test_valid_signature_no_matching_shipment(self, api_client):
        """
        Webhook with valid signature but unmatched shipment_reference must
        still return 200 (carrier should not retry) but shipment_matched=False.
        No TrackingEvent is created since shipment FK is NOT NULL.
        """
        carrier = CarrierFactory(
            is_active=True,
            service_metadata={"webhook_secret": "test-secret-123"},
        )
        body = {
            "webhook_id": "evt_test_unmatched",
            "event_type": "vessel.departed",
            "shipment_reference": "GT-NOTFOUND",
            "event_timestamp": timezone.now().isoformat(),
            "payload": {},
        }
        body_bytes = json.dumps(body).encode()
        signature = self._sign(body_bytes, "test-secret-123")

        response = api_client.post(
            f"/api/v1/webhooks/carrier/{carrier.carrier_code}/",
            data=body_bytes,
            content_type="application/json",
            HTTP_X_SIGNATURE_SHA256=signature,
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["shipment_matched"] is False
        assert TrackingEvent.objects.filter(
            raw_payload__webhook_id="evt_test_unmatched"
        ).count() == 0

    def test_invalid_signature_rejected(self, api_client):
        """
        ATTACK: forged webhook with wrong signature must be rejected with 401,
        not 400 — this is an authentication failure.
        """
        carrier = CarrierFactory(
            is_active=True,
            service_metadata={"webhook_secret": "test-secret-123"},
        )
        body = {
            "webhook_id": "evt_test_002",
            "event_type": "vessel.departed",
            "shipment_reference": "GT-NOTFOUND",
            "event_timestamp": timezone.now().isoformat(),
        }
        body_bytes = json.dumps(body).encode()

        response = api_client.post(
            f"/api/v1/webhooks/carrier/{carrier.carrier_code}/",
            data=body_bytes,
            content_type="application/json",
            HTTP_X_SIGNATURE_SHA256="sha256=deadbeef00000000",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_signature_rejected(self, api_client):
        """Webhook with no signature header at all must be rejected."""
        carrier = CarrierFactory(
            is_active=True,
            service_metadata={"webhook_secret": "test-secret-123"},
        )
        body_bytes = json.dumps({"webhook_id": "evt_003"}).encode()

        response = api_client.post(
            f"/api/v1/webhooks/carrier/{carrier.carrier_code}/",
            data=body_bytes,
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_duplicate_webhook_id_ignored(self, api_client):
        """
        Same webhook_id sent twice must not create duplicate TrackingEvents.
        Carrier systems often retry webhooks on timeout.
        """
        carrier = CarrierFactory(
            is_active=True,
            service_metadata={"webhook_secret": "test-secret-123"},
        )
        shipment = ShipmentFactory(carrier=carrier)

        body = {
            "webhook_id": "evt_test_dup",
            "event_type": "vessel.departed",
            "shipment_reference": shipment.tracking_number,
            "event_timestamp": timezone.now().isoformat(),
            "payload": {},
        }
        body_bytes = json.dumps(body).encode()
        signature = self._sign(body_bytes, "test-secret-123")

        url = f"/api/v1/webhooks/carrier/{carrier.carrier_code}/"
        r1 = api_client.post(
            url, data=body_bytes, content_type="application/json",
            HTTP_X_SIGNATURE_SHA256=signature,
        )
        r2 = api_client.post(
            url, data=body_bytes, content_type="application/json",
            HTTP_X_SIGNATURE_SHA256=signature,
        )

        assert r1.status_code == status.HTTP_200_OK
        assert r2.status_code == status.HTTP_200_OK
        assert TrackingEvent.objects.filter(
            raw_payload__webhook_id="evt_test_dup"
        ).count() == 1

    def test_unknown_carrier_code_rejected(self, api_client):
        """Webhook for a non-existent carrier_code must return 401, not 404."""
        response = api_client.post(
            "/api/v1/webhooks/carrier/NONEXISTENT/",
            data=b'{"webhook_id": "x"}',
            content_type="application/json",
            HTTP_X_SIGNATURE_SHA256="sha256=abc",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestHealthCheck:

    def test_health_check_no_auth_required(self, api_client):
        """
        Health check must be accessible without authentication.
        In test environment Celery worker is not running — status can be
        'ok' or 'degraded', but HTTP must always be 200 as long as DB is up.
        """
        response = api_client.get("/api/v1/health/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["db"] == "ok"
        assert response.json()["status"] in ("ok", "degraded")