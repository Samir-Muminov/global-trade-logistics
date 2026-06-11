"""
apps/users/serializers.py

Auth serializers: registration, JWT with custom claims, email verification,
password reset, profile.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.mail import send_mail
from django.conf import settings
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Registration serializer. Email + password only.
    Password confirmed client-side; we validate strength server-side.

    No username field — email is the identity.
    On success: creates CustomUser + UserProfile (via signal) + sends verification email.
    Password is never returned in response — write_only enforced.
    """

    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )
    user_type = serializers.ChoiceField(
        choices=["SHIPPER", "CONSIGNEE", "FF"],
        default="SHIPPER",
        help_text="Staff and CARRIER_STAFF users are created by admins only.",
    )

    class Meta:
        model = User
        fields = (
            "email",
            "first_name",
            "last_name",
            "password",
            "password_confirm",
            "user_type",
        )

    def validate_email(self, value: str) -> str:
        # Normalise to lowercase — prevents duplicate accounts via case variation
        return value.lower()

    def validate(self, attrs: dict) -> dict:
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        # Django's built-in password validators (length, common passwords, etc.)
        validate_password(attrs["password"])
        return attrs

    def create(self, validated_data: dict) -> User:
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            user_type=validated_data.get("user_type", "SHIPPER"),
            is_email_verified=False,
        )
        self._send_verification_email(user)
        return user

    def _send_verification_email(self, user: User) -> None:
        # Signed token — stateless, no DB storage needed.
        # Includes timestamp for expiry check in verification view.
        token = signing.dumps(
            {"user_id": str(user.id)},
            salt="email-verification",
        )
        verify_url = f"{settings.FRONTEND_URL}/verify-email/?token={token}"
        send_mail(
            subject="Verify your Global Trade Platform account",
            message=f"Click to verify your email: {verify_url}\n\nLink expires in 24 hours.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Read-only user profile for GET /api/v1/auth/me/
    Exposes safe fields only — no password hash, no failed_login_count.
    """

    company_name = serializers.SerializerMethodField()
    carrier_name = serializers.SerializerMethodField()
    user_type_display = serializers.CharField(
        source="get_user_type_display", read_only=True
    )

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "user_type",
            "user_type_display",
            "company_name",
            "carrier_name",
            "is_email_verified",
            "date_joined",
        )
        read_only_fields = fields

    def get_company_name(self, obj: User) -> str | None:
        if obj.company_id:
            return obj.company.legal_name
        return None

    def get_carrier_name(self, obj: User) -> str | None:
        if obj.carrier_id:
            return obj.carrier.carrier_name
        return None


class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom JWT serializer that adds user_type, company_id, and carrier_id
    to the token payload.

    WHY: Eliminates DB queries in permission checks.
    Without this: IsShipmentOwnerOrStaff must query DB to get user.company_id.
    With this: company_id is in the JWT — permission check reads from token, no DB.

    At 100 req/s, this saves 100 DB queries/second on authenticated endpoints.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Custom claims
        token["user_type"] = user.user_type
        token["company_id"] = str(user.company_id) if user.company_id else None
        token["carrier_id"] = str(user.carrier_id) if user.carrier_id else None
        token["is_email_verified"] = user.is_email_verified
        return token


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    POST /api/v1/auth/password-reset/
    Accepts email, sends reset link if account exists.

    SECURITY: always returns 200 regardless of whether email exists.
    Returning 404 for unknown emails enables user enumeration.
    """

    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    POST /api/v1/auth/password-reset/confirm/
    Validates signed token + new password.
    Token is one-time use (Django's PasswordResetTokenGenerator invalidates
    after use by hashing last_login into the token).
    """

    token = serializers.CharField()
    uid = serializers.CharField()
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value