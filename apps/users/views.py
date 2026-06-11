"""
apps/users/views.py

Auth endpoints: registration, email verification, password reset, profile.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.core.mail import send_mail
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.api.v1.throttling import AnalyticsThrottle
from apps.users.serializers import (
    MyTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
)

User = get_user_model()

# 24 hours in seconds — verification token max age
EMAIL_VERIFICATION_MAX_AGE = 86400


class AuthRateThrottle(AnalyticsThrottle):
    """
    Strict throttle for auth endpoints: 5/minute.
    Prevents credential stuffing and brute force on login endpoint.
    """
    scope = "auth"

    def get_rate(self) -> str:
        from django.conf import settings
        return getattr(settings, "THROTTLE_RATES", {}).get("auth", "5/minute")


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    POST /api/v1/auth/token/

    JWT login with custom claims (user_type, company_id, carrier_id).
    Rate limited to 5/minute per IP to prevent brute force.
    django-axes handles lockout after 5 failures.
    """

    serializer_class = MyTokenObtainPairSerializer
    throttle_classes = [AuthRateThrottle]


class UserRegistrationView(APIView):
    """
    POST /api/v1/auth/register/

    Creates a new user account and sends verification email.
    No authentication required — this is the entry point.
    Rate limited to prevent account creation spam.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                "detail": "Account created. Check your email to verify your address.",
                "email": user.email,
            },
            status=status.HTTP_201_CREATED,
        )


class EmailVerificationView(APIView):
    """
    GET /api/v1/auth/verify-email/?token=xxx

    Verifies email using signed token. Token is stateless — no DB storage.
    Expires after 24 hours via Django's signing framework timestamp.

    On success: sets is_email_verified=True, returns 200.
    On invalid/expired token: returns 400 with clear message.
    """

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        token = request.query_params.get("token")
        if not token:
            return Response(
                {"detail": "Verification token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = signing.loads(
                token,
                salt="email-verification",
                max_age=EMAIL_VERIFICATION_MAX_AGE,
            )
        except SignatureExpired:
            return Response(
                {"detail": "Verification link has expired. Request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BadSignature:
            return Response(
                {"detail": "Invalid verification token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(id=data["user_id"])
        except User.DoesNotExist:
            return Response(
                {"detail": "Invalid verification token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.is_email_verified:
            return Response(
                {"detail": "Email already verified."},
                status=status.HTTP_200_OK,
            )

        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])

        return Response(
            {"detail": "Email verified successfully. You can now log in."},
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestView(APIView):
    """
    POST /api/v1/auth/password-reset/

    Sends password reset email if account exists.
    ALWAYS returns 200 — never reveals whether email exists (prevents enumeration).
    Rate limited: 5/minute.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request, *args, **kwargs):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(email=email, is_active=True)
            self._send_reset_email(user)
        except User.DoesNotExist:
            # Intentional: don't reveal whether email exists
            pass

        return Response(
            {"detail": "If an account with that email exists, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )

    def _send_reset_email(self, user: User) -> None:
        token_generator = PasswordResetTokenGenerator()
        token = token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_url = f"{settings.FRONTEND_URL}/reset-password/?uid={uid}&token={token}"
        send_mail(
            subject="Reset your Global Trade Platform password",
            message=f"Click to reset your password: {reset_url}\n\nLink expires in 1 hour.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )


class PasswordResetConfirmView(APIView):
    """
    POST /api/v1/auth/password-reset/confirm/

    Validates token + sets new password.
    Token is one-time use — Django's PasswordResetTokenGenerator invalidates
    after use by including last_login in the token hash.
    Token expires in 1 hour (Django default PASSWORD_RESET_TIMEOUT).
    """

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            uid = force_str(urlsafe_base64_decode(serializer.validated_data["uid"]))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, User.DoesNotExist):
            return Response(
                {"detail": "Invalid reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token_generator = PasswordResetTokenGenerator()
        if not token_generator.check_token(user, serializer.validated_data["token"]):
            return Response(
                {"detail": "Invalid or expired reset link. Request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        return Response(
            {"detail": "Password reset successfully. You can now log in."},
            status=status.HTTP_200_OK,
        )


class UserMeView(APIView):
    """
    GET /api/v1/auth/me/

    Returns profile of the authenticated user.
    Uses select_related to avoid N+1 on company and carrier.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = (
            User.objects
            .select_related("company", "carrier")
            .get(pk=request.user.pk)
        )
        serializer = UserProfileSerializer(user)
        return Response(serializer.data)
    