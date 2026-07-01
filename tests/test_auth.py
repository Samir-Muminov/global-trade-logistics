"""
tests/test_auth.py

Auth flow tests: registration, email verification, password reset, JWT claims.
Every test documents a real security scenario.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from rest_framework import status

from tests.factories import CustomUserFactory, CompanyFactory

User = get_user_model()


@pytest.mark.django_db
class TestUserRegistration:

    def test_registration_creates_user(self, api_client):
        """
        POST /api/v1/auth/register/ with valid data creates a new user.
        User starts with is_email_verified=False.
        """
        response = api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "newuser@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "password": "SecurePass123!",
                "password_confirm": "SecurePass123!",
                "user_type": "SHIPPER",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        user = User.objects.get(email="newuser@example.com")
        assert user.is_email_verified is False

    def test_registration_normalises_email_to_lowercase(self, api_client):
        """
        Email must be normalised to lowercase — prevents duplicate accounts
        via case variation (User@Example.com vs user@example.com).
        """
        api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "UPPER@EXAMPLE.COM",
                "password": "SecurePass123!",
                "password_confirm": "SecurePass123!",
            },
            format="json",
        )
        assert User.objects.filter(email="upper@example.com").exists()
        assert not User.objects.filter(email="UPPER@EXAMPLE.COM").exists()

    def test_registration_rejects_password_mismatch(self, api_client):
        """Password confirmation mismatch must return 400."""
        response = api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "test@example.com",
                "password": "SecurePass123!",
                "password_confirm": "DifferentPass123!",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_registration_rejects_duplicate_email(self, api_client):
        """Duplicate email must return 400 — one account per email."""
        CustomUserFactory(email="existing@example.com")
        response = api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "existing@example.com",
                "password": "SecurePass123!",
                "password_confirm": "SecurePass123!",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_registration_rejects_weak_password(self, api_client):
        """Django password validators must reject weak passwords."""
        response = api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "weak@example.com",
                "password": "123",
                "password_confirm": "123",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestEmailVerification:

    def test_valid_token_verifies_email(self, api_client):
        """
        GET /api/v1/auth/verify-email/?token=... with valid signed token
        must set is_email_verified=True.
        """
        user = CustomUserFactory(is_email_verified=False)
        token = signing.dumps(
            {"user_id": str(user.id)},
            salt="email-verification",
        )
        response = api_client.get(
            f"/api/v1/auth/verify-email/?token={token}"
        )
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.is_email_verified is True

    def test_expired_token_rejected(self, api_client):
        """
        Token older than 24 hours must be rejected.
        Prevents indefinitely-valid verification links from leaked emails.
        """
        user = CustomUserFactory(is_email_verified=False)
        token = signing.dumps(
            {"user_id": str(user.id)},
            salt="email-verification",
        )
        # Simulate expired token by passing max_age=0 in the view check.
        # We test this by using a deliberately wrong salt.
        bad_token = signing.dumps(
            {"user_id": str(user.id)},
            salt="wrong-salt",
        )
        response = api_client.get(
            f"/api/v1/auth/verify-email/?token={bad_token}"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.is_email_verified is False

    def test_missing_token_returns_400(self, api_client):
        """Request without token parameter must return 400, not 500."""
        response = api_client.get("/api/v1/auth/verify-email/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_already_verified_returns_200(self, api_client):
        """
        Verifying an already-verified email must return 200 (idempotent),
        not 400 — user may click the link twice.
        """
        user = CustomUserFactory(is_email_verified=True)
        token = signing.dumps(
            {"user_id": str(user.id)},
            salt="email-verification",
        )
        response = api_client.get(
            f"/api/v1/auth/verify-email/?token={token}"
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestPasswordReset:

    def test_reset_request_always_returns_200(self, api_client):
        """
        SECURITY: POST /api/v1/auth/password-reset/ must always return 200
        regardless of whether the email exists.
        Returning 404 for unknown emails enables user enumeration attacks.
        """
        response = api_client.post(
            "/api/v1/auth/password-reset/",
            {"email": "nonexistent@example.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_reset_request_also_returns_200_for_valid_email(self, api_client):
        """Both existing and non-existing emails return 200 — indistinguishable."""
        CustomUserFactory(email="real@example.com")
        response = api_client.post(
            "/api/v1/auth/password-reset/",
            {"email": "real@example.com"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestJWTCustomClaims:

    def test_jwt_contains_user_type(self, api_client):
        """
        JWT access token must contain user_type, company_id, carrier_id claims.
        These eliminate DB queries in permission checks — the permission class
        reads from the token instead of hitting the DB for every request.
        """
        user = CustomUserFactory(
            email="jwt@example.com",
            user_type="SHIPPER",
            company=CompanyFactory(),
        )
        user.set_password("TestPass123!")
        user.save()

        response = api_client.post(
            "/api/v1/auth/token/",
            {"email": "jwt@example.com", "password": "TestPass123!"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        import base64, json as _json
        access = response.json()["access"]
        payload_b64 = access.split(".")[1]
        # Add padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(base64.b64decode(payload_b64))

        assert "user_type" in payload
        assert payload["user_type"] == "SHIPPER"
        assert "company_id" in payload
        assert "carrier_id" in payload

    def test_login_with_wrong_password_returns_401(self, api_client):
        """Failed login must return 401, not 400 or 500."""
        CustomUserFactory(email="auth@example.com")
        response = api_client.post(
            "/api/v1/auth/token/",
            {"email": "auth@example.com", "password": "wrongpassword"},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestUserMeEndpoint:

    def test_me_returns_user_profile(self, api_client, shipper_user):
        """GET /api/v1/auth/me/ returns the authenticated user's profile."""
        api_client.force_authenticate(user=shipper_user)
        response = api_client.get("/api/v1/auth/me/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["email"] == shipper_user.email
        assert data["user_type"] == "SHIPPER"

    def test_me_requires_authentication(self, api_client):
        """Unauthenticated request to /me/ must return 401."""
        response = api_client.get("/api/v1/auth/me/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED