"""
apps/logistics/migrations/0003_add_idempotency_key.py

Adds a GIN index on Shipment.custom_attributes to support fast idempotency
key lookups via JSONB containment queries.

The idempotency key is stored as:
  custom_attributes = {"idempotency_key": "uuid-string", ...}

The existing shipment_custom_attr_gin_idx already covers this field —
this migration documents the dependency and adds a partial expression index
for even faster idempotency-specific lookups.

⚠️ RISK: CREATE INDEX CONCURRENTLY cannot run inside a transaction.
Django wraps migrations in transactions by default.
We set atomic = False to allow concurrent index creation.

Rollback: DROP INDEX CONCURRENTLY idx_shipment_idempotency_key
Safe: index is non-blocking during creation.
"""

from django.db import migrations


CREATE_IDEMPOTENCY_INDEX = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_shipment_idempotency_key
ON shipments ((custom_attributes->>'idempotency_key'))
WHERE custom_attributes ? 'idempotency_key';
"""

DROP_IDEMPOTENCY_INDEX = """
DROP INDEX CONCURRENTLY IF EXISTS idx_shipment_idempotency_key;
"""

CREATE_WEBHOOK_INDEX = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tracking_webhook_id
ON tracking_events ((raw_payload->>'webhook_id'))
WHERE raw_payload ? 'webhook_id';
"""

DROP_WEBHOOK_INDEX = """
DROP INDEX CONCURRENTLY IF EXISTS idx_tracking_webhook_id;
"""


class Migration(migrations.Migration):

    # Must be False because CREATE INDEX CONCURRENTLY cannot run in a transaction
    atomic = False

    dependencies = [
        ("logistics", "0002_materialized_views"),
    ]

    operations = [
        # Idempotency key index on Shipment.custom_attributes
        # Serves: _check_idempotency_key() lookup in ShipmentCreateView
        # Query: custom_attributes__idempotency_key=str(key)
        # Without this: full GIN scan of custom_attributes for every POST
        # With this: expression index on the specific key — O(log n)
        migrations.RunSQL(
            sql=CREATE_IDEMPOTENCY_INDEX,
            reverse_sql=DROP_IDEMPOTENCY_INDEX,
        ),
        # Webhook deduplication index on TrackingEvent.raw_payload
        # Serves: _check_webhook_idempotency() lookup in WebhookReceiveView
        # Query: raw_payload__webhook_id=webhook_id
        migrations.RunSQL(
            sql=CREATE_WEBHOOK_INDEX,
            reverse_sql=DROP_WEBHOOK_INDEX,
        ),
    ]