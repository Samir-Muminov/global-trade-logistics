"""
apps/users/admin.py

Django admin registration for CustomUser and UserProfile.
Without this, superusers cannot manage users via /admin/ panel.
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.users.models import CustomUser, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fields = (
        "phone_number",
        "timezone",
        "last_login_ip",
        "last_login_at",
        "failed_login_count",
    )
    readonly_fields = ("last_login_ip", "last_login_at", "failed_login_count")


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    """
    Admin for CustomUser — email as identity, no username field.
    Extends Django's built-in UserAdmin but removes username references.
    """

    inlines = [UserProfileInline]

    # Columns shown in user list
    list_display = (
        "email",
        "first_name",
        "last_name",
        "user_type",
        "is_active",
        "is_staff",
        "is_email_verified",
        "date_joined",
    )
    list_filter = ("user_type", "is_active", "is_staff", "is_email_verified")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("-date_joined",)

    # Fields shown when editing a user
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal Info",
            {"fields": ("first_name", "last_name", "user_type")},
        ),
        (
            "Company & Carrier",
            {"fields": ("company", "carrier")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "is_email_verified",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    # Fields shown when creating a new user via admin
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "user_type",
                    "is_active",
                    "is_staff",
                ),
            },
        ),
    )

    # CustomUser has no username field
    USERNAME_FIELD = "email"