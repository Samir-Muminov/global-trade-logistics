"""
apps/users/urls.py — auth endpoints
"""
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.users.views import (
    CustomTokenObtainPairView,
    EmailVerificationView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    UserMeView,
    UserRegistrationView,
)

app_name = "users"

urlpatterns = [
    path("auth/register/", UserRegistrationView.as_view(), name="register"),
    path("auth/token/", CustomTokenObtainPairView.as_view(), name="token_obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/verify-email/", EmailVerificationView.as_view(), name="verify_email"),
    path("auth/password-reset/", PasswordResetRequestView.as_view(), name="password_reset"),
    path("auth/password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("auth/me/", UserMeView.as_view(), name="me"),
]