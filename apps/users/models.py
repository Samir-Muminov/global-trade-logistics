"""
apps/users/models.py

Custom User model for Global Trade & Logistics Analytics Platform.

⚠️ MIGRATION RISK: AUTH_USER_MODEL must be set BEFORE the first migration runs.
Changing AUTH_USER_MODEL after migrations exist requires wiping the DB or
a complex multi-step migration. This model must be the first thing set up
in a fresh environment.

Dependency chain:
  users.0001_initial → logistics.0001_initial (Company/Carrier exist)
  logistics.0004_add_user_fks → users.0001_initial (Shipment.created_by FK)
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from apps.users.managers import CustomUserManager


class UserType(models.TextChoices):
    SHIPPER = "SHIPPER", "Shipper"
    CONSIGNEE = "CONSIGNEE", "Consignee"
    CARRIER_STAFF = "CARRIER_STAFF", "Carrier Staff"
    FREIGHT_FORWARDER = "FF", "Freight Forwarder"
    STAFF = "STAFF", "Internal Staff"


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Platform user. Email is the identity — no username field.

    company FK: shippers, consignees, freight forwarders have a company.
    carrier FK: carrier_staff users are linked to a specific carrier.
    Staff users (is_staff=True, user_type=STAFF) have neither.

    is_email_verified: unverified users cannot access write endpoints.
    Verification is done via signed token (stateless — not stored in DB).
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_column="id",
    )
    email = models.EmailField(
        unique=True,
        db_column="email",
        help_text="Primary identity. Used for login and all communications.",
    )
    first_name = models.CharField(max_length=150, blank=True, db_column="first_name")
    last_name = models.CharField(max_length=150, blank=True, db_column="last_name")

    user_type = models.CharField(
        max_length=16,
        choices=UserType.choices,
        default=UserType.SHIPPER,
        db_column="user_type",
    )

    # Company FK — nullable for staff users
    company = models.ForeignKey(
        "logistics.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        db_column="company_id",
        help_text="The company this user represents. NULL for internal staff.",
    )

    # Carrier FK — only for CARRIER_STAFF users
    carrier = models.ForeignKey(
        "logistics.Carrier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_users",
        db_column="carrier_id",
        help_text="The carrier this user works for. NULL for non-carrier users.",
    )

    is_active = models.BooleanField(default=True, db_column="is_active")
    is_staff = models.BooleanField(default=False, db_column="is_staff")
    is_email_verified = models.BooleanField(
        default=False,
        db_column="is_email_verified",
        help_text="Unverified users cannot access write endpoints.",
    )

    date_joined = models.DateTimeField(auto_now_add=True, db_column="date_joined")

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # email + password only for createsuperuser

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        indexes = [
            models.Index(fields=["user_type", "is_active"], name="user_type_active_idx"),
            models.Index(fields=["company_id"], name="user_company_idx"),
            models.Index(fields=["carrier_id"], name="user_carrier_idx"),
        ]
        constraints = [
            # CARRIER_STAFF must have a carrier FK
            models.CheckConstraint(
                condition=(
                    ~models.Q(user_type="CARRIER_STAFF") | ~models.Q(carrier__isnull=True)
                ),
                name="user_carrier_staff_requires_carrier",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.get_user_type_display()})"

    def __repr__(self) -> str:
        return f"<CustomUser id={self.id} email={self.email!r} type={self.user_type}>"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email


class UserProfile(models.Model):
    """
    Extended profile data for CustomUser.
    Separated from CustomUser to keep the auth model lean.
    OneToOne relationship — always created alongside CustomUser via signal.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_column="id",
    )
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="profile",
        db_column="user_id",
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        db_column="phone_number",
        help_text="E.164 format e.g. +12125551234",
    )
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        db_column="timezone",
        help_text="IANA timezone identifier",
    )
    notification_preferences = models.JSONField(
        default=dict,
        blank=True,
        db_column="notification_preferences",
        help_text="Per-user notification settings: email, sms, webhook toggles",
    )
    last_login_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        db_column="last_login_ip",
        help_text="IP of last successful login — for audit and anomaly detection",
    )
    last_login_at = models.DateTimeField(
        null=True,
        blank=True,
        db_column="last_login_at",
    )
    failed_login_count = models.PositiveIntegerField(
        default=0,
        db_column="failed_login_count",
        help_text="Mirrored from django-axes for API exposure. Axes manages the actual lockout.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_column="created_at")
    updated_at = models.DateTimeField(auto_now=True, db_column="updated_at")

    class Meta:
        db_table = "user_profiles"
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self) -> str:
        return f"Profile({self.user.email})"

    def __repr__(self) -> str:
        return f"<UserProfile user={self.user.email!r}>"