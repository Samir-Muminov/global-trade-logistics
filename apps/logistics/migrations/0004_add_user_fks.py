"""
apps/logistics/migrations/0004_add_user_fks.py

Adds created_by FK to Shipment — links each shipment to the user who booked it.

⚠️ DEPENDENCY CHAIN:
This migration depends on users.0001_initial because Shipment.created_by
is a FK to CustomUser. users.0001_initial must run before this migration.

created_by is nullable + SET_NULL:
- Nullable: existing shipments were created before this field existed —
  they have no creator. Forcing NOT NULL would break migrate on existing data.
- SET_NULL on_delete: if a user account is deleted (GDPR request), the
  shipment record is preserved — only the creator reference is cleared.
  CASCADE would delete all shipments when a user is deleted, which is
  unacceptable for financial/audit records.

Rollback: python manage.py migrate logistics 0003
Safe: removing a nullable column never loses data.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("logistics", "0003_add_idempotency_key"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="shipment",
            name="created_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="created_shipments",
                db_column="created_by_id",
                help_text="User who booked this shipment via API.",
            ),
        ),
    ]