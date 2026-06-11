"""
apps/logistics/migrations/0002_materialized_views.py

Creates 3 materialized views and their unique indexes.

⚠️ RISK — Initial CREATE:
CREATE MATERIALIZED VIEW acquires a ShareUpdateExclusiveLock on the underlying
tables during build. This does NOT block reads or writes — only other schema
changes. At 10M rows the initial build takes ~30-90 seconds.
Schedule this migration during low-traffic window on first deploy.

⚠️ RISK — CONCURRENT refresh requirement:
REFRESH MATERIALIZED VIEW CONCURRENTLY requires a unique index on each view.
If the unique index is dropped, CONCURRENT refresh will fail with:
"ERROR: cannot refresh materialized view concurrently without a unique index"
The unique indexes below are therefore load-bearing — do not drop them.

Rollback: python manage.py migrate logistics 0001
Reverse SQL drops views and indexes in correct dependency order.
"""

from django.db import migrations


# ── View SQL ───────────────────────────────────────────────────────────────────

CREATE_MV_CARRIER_DAILY_STATS = """
CREATE MATERIALIZED VIEW mv_carrier_daily_stats AS
SELECT
    s.carrier_id,
    DATE(s.departure_date AT TIME ZONE 'UTC')  AS stat_date,
    COUNT(s.id)                                AS shipment_count,
    COUNT(s.id) FILTER (
        WHERE s.estimated_arrival < NOW()
          AND s.status NOT IN ('DELIVERED', 'CANCELLED')
    )                                          AS delay_count,
    AVG(s.declared_value)                      AS avg_declared_value,
    COALESCE(SUM(c.total_weight), 0)           AS total_weight_kg,
    COUNT(te.id) FILTER (WHERE te.is_exception = TRUE) AS exception_count
FROM shipments s
LEFT JOIN (
    SELECT shipment_id, SUM(gross_weight_kg) AS total_weight
    FROM cargo
    GROUP BY shipment_id
) c ON c.shipment_id = s.id
LEFT JOIN tracking_events te ON te.shipment_id = s.id
WHERE s.departure_date IS NOT NULL
GROUP BY s.carrier_id, DATE(s.departure_date AT TIME ZONE 'UTC')
WITH DATA;
"""

DROP_MV_CARRIER_DAILY_STATS = "DROP MATERIALIZED VIEW IF EXISTS mv_carrier_daily_stats;"

CREATE_IDX_CARRIER_DAILY_STATS = """
CREATE UNIQUE INDEX idx_mv_carrier_daily_stats_unique
ON mv_carrier_daily_stats (carrier_id, stat_date);
"""

DROP_IDX_CARRIER_DAILY_STATS = """
DROP INDEX IF EXISTS idx_mv_carrier_daily_stats_unique;
"""

# ── Route Monthly Performance ──────────────────────────────────────────────────

CREATE_MV_ROUTE_MONTHLY = """
CREATE MATERIALIZED VIEW mv_route_monthly_performance AS
SELECT
    s.route_id,
    DATE_TRUNC('month', s.departure_date)      AS stat_month,
    COUNT(s.id)                                AS shipment_count,
    AVG(
        EXTRACT(EPOCH FROM (s.actual_arrival - s.departure_date)) / 86400.0
    )                                          AS avg_transit_days,
    COUNT(s.id) FILTER (
        WHERE s.actual_arrival <= s.estimated_arrival
          AND s.status = 'DELIVERED'
    ) * 100.0 / NULLIF(
        COUNT(s.id) FILTER (WHERE s.status = 'DELIVERED'), 0
    )                                          AS on_time_rate_pct,
    COALESCE(SUM(s.declared_value), 0)         AS total_declared_value
FROM shipments s
WHERE s.departure_date IS NOT NULL
GROUP BY s.route_id, DATE_TRUNC('month', s.departure_date)
WITH DATA;
"""

DROP_MV_ROUTE_MONTHLY = "DROP MATERIALIZED VIEW IF EXISTS mv_route_monthly_performance;"

CREATE_IDX_ROUTE_MONTHLY = """
CREATE UNIQUE INDEX idx_mv_route_monthly_unique
ON mv_route_monthly_performance (route_id, stat_month);
"""

DROP_IDX_ROUTE_MONTHLY = "DROP INDEX IF EXISTS idx_mv_route_monthly_unique;"

# ── Port Congestion ────────────────────────────────────────────────────────────

CREATE_MV_PORT_CONGESTION = """
CREATE MATERIALIZED VIEW mv_port_congestion AS
SELECT
    pc.port_id,
    DATE_TRUNC('week', pc.scheduled_arrival)   AS stat_week,
    COUNT(pc.id)                               AS arrival_count,
    AVG(pc.arrival_delay_hours)                AS avg_delay_hours,
    COUNT(te.id) FILTER (WHERE te.is_exception = TRUE) * 100.0
        / NULLIF(COUNT(te.id), 0)              AS exception_rate_pct
FROM port_calls pc
LEFT JOIN tracking_events te
    ON te.port_id = pc.port_id
   AND te.event_time BETWEEN
       pc.scheduled_arrival - INTERVAL '2 days'
   AND pc.scheduled_arrival + INTERVAL '2 days'
WHERE pc.scheduled_arrival IS NOT NULL
GROUP BY pc.port_id, DATE_TRUNC('week', pc.scheduled_arrival)
WITH DATA;
"""

DROP_MV_PORT_CONGESTION = "DROP MATERIALIZED VIEW IF EXISTS mv_port_congestion;"

CREATE_IDX_PORT_CONGESTION = """
CREATE UNIQUE INDEX idx_mv_port_congestion_unique
ON mv_port_congestion (port_id, stat_week);
"""

DROP_IDX_PORT_CONGESTION = "DROP INDEX IF EXISTS idx_mv_port_congestion_unique;"


class Migration(migrations.Migration):

    dependencies = [
        ("logistics", "0001_initial"),
    ]

    operations = [
        # Carrier daily stats
        migrations.RunSQL(
            sql=CREATE_MV_CARRIER_DAILY_STATS,
            reverse_sql=DROP_MV_CARRIER_DAILY_STATS,
        ),
        migrations.RunSQL(
            sql=CREATE_IDX_CARRIER_DAILY_STATS,
            reverse_sql=DROP_IDX_CARRIER_DAILY_STATS,
        ),
        # Route monthly performance
        migrations.RunSQL(
            sql=CREATE_MV_ROUTE_MONTHLY,
            reverse_sql=DROP_MV_ROUTE_MONTHLY,
        ),
        migrations.RunSQL(
            sql=CREATE_IDX_ROUTE_MONTHLY,
            reverse_sql=DROP_IDX_ROUTE_MONTHLY,
        ),
        # Port congestion
        migrations.RunSQL(
            sql=CREATE_MV_PORT_CONGESTION,
            reverse_sql=DROP_MV_PORT_CONGESTION,
        ),
        migrations.RunSQL(
            sql=CREATE_IDX_PORT_CONGESTION,
            reverse_sql=DROP_IDX_PORT_CONGESTION,
        ),
    ]