"""
apps/users/migrations/0001_initial.py

⚠️ MIGRATION DEPENDENCY:
This migration depends on logistics.0001_initial because CustomUser has
ForeignKeys to Company and Carrier. logistics.0001_initial must run first.

Rollback: python manage.py migrate users zero
Safe: no data exists yet on first deploy.
"""

import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("logistics", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomUser",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                ("last_login", models.DateTimeField(blank=True, null=True, verbose_name="last login")),
                ("is_superuser", models.BooleanField(default=False)),
                ("email", models.EmailField(unique=True, db_column="email")),
                ("first_name", models.CharField(max_length=150, blank=True, db_column="first_name")),
                ("last_name", models.CharField(max_length=150, blank=True, db_column="last_name")),
                ("user_type", models.CharField(max_length=16, choices=[("SHIPPER","Shipper"),("CONSIGNEE","Consignee"),("CARRIER_STAFF","Carrier Staff"),("FF","Freight Forwarder"),("STAFF","Internal Staff")], default="SHIPPER", db_column="user_type")),
                ("company", models.ForeignKey(to="logistics.Company", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="users", db_column="company_id")),
                ("carrier", models.ForeignKey(to="logistics.Carrier", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="staff_users", db_column="carrier_id")),
                ("is_active", models.BooleanField(default=True, db_column="is_active")),
                ("is_staff", models.BooleanField(default=False, db_column="is_staff")),
                ("is_email_verified", models.BooleanField(default=False, db_column="is_email_verified")),
                ("date_joined", models.DateTimeField(auto_now_add=True, db_column="date_joined")),
            ],
            options={"db_table": "users", "verbose_name": "User", "verbose_name_plural": "Users"},
        ),
        migrations.AddIndex(model_name="customuser", index=models.Index(fields=["user_type", "is_active"], name="user_type_active_idx")),
        migrations.AddIndex(model_name="customuser", index=models.Index(fields=["company_id"], name="user_company_idx")),
        migrations.AddIndex(model_name="customuser", index=models.Index(fields=["carrier_id"], name="user_carrier_idx")),
        migrations.AddConstraint(
            model_name="customuser",
            constraint=models.CheckConstraint(
                condition=~models.Q(user_type="CARRIER_STAFF") | ~models.Q(carrier__isnull=True),
                name="user_carrier_staff_requires_carrier",
            ),
        ),
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("user", models.OneToOneField(to="users.CustomUser", on_delete=django.db.models.deletion.CASCADE, related_name="profile", db_column="user_id")),
                ("phone_number", models.CharField(max_length=20, blank=True, db_column="phone_number")),
                ("timezone", models.CharField(max_length=64, default="UTC", db_column="timezone")),
                ("notification_preferences", models.JSONField(default=dict, blank=True, db_column="notification_preferences")),
                ("last_login_ip", models.GenericIPAddressField(null=True, blank=True, db_column="last_login_ip")),
                ("last_login_at", models.DateTimeField(null=True, blank=True, db_column="last_login_at")),
                ("failed_login_count", models.PositiveIntegerField(default=0, db_column="failed_login_count")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
            ],
            options={"db_table": "user_profiles", "verbose_name": "User Profile", "verbose_name_plural": "User Profiles"},
        ),
    ]