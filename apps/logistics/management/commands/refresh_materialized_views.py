"""
apps/logistics/management/commands/refresh_materialized_views.py

Refreshes PostgreSQL materialized views using CONCURRENTLY to avoid read locks.

⚠️ CRITICAL REQUIREMENT:
REFRESH MATERIALIZED VIEW CONCURRENTLY requires a UNIQUE index on each view.
These indexes are created in migration 0002_materialized_views.py.
If the indexes are dropped, this command will fail — do not drop them.

Schedule:
  mv_carrier_daily_stats      — every 15 minutes (cron or Celery beat)
  mv_route_monthly_performance — nightly at 02:00 UTC
  mv_port_congestion          — every hour
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

VIEWS = {
    "carrier_daily": "mv_carrier_daily_stats",
    "route_monthly": "mv_route_monthly_performance",
    "port_congestion": "mv_port_congestion",
    "ALL": None,
}

VALID_VIEWS = list(VIEWS.keys())


class Command(BaseCommand):
    help = (
        "Refresh PostgreSQL materialized views using CONCURRENTLY (no read locks). "
        "Example: python manage.py refresh_materialized_views --view carrier_daily"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--view",
            type=str,
            default="ALL",
            choices=VALID_VIEWS,
            help=(
                "Which view to refresh. "
                f"Choices: {', '.join(VALID_VIEWS)}. "
                "Default: ALL."
            ),
        )

    def handle(self, *args, **options):
        view_arg = options["view"]

        if view_arg == "ALL":
            views_to_refresh = [
                (k, v) for k, v in VIEWS.items() if k != "ALL"
            ]
        else:
            views_to_refresh = [(view_arg, VIEWS[view_arg])]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n── Refreshing {len(views_to_refresh)} materialized view(s) ──\n"
            )
        )

        errors = []
        for view_key, view_name in views_to_refresh:
            self.stdout.write(f"── Refreshing {view_name} ──")
            start = time.monotonic()

            try:
                self._refresh_view(view_name)
                elapsed = time.monotonic() - start
                row_count = self._get_row_count(view_name)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"   ✓ {view_name}: {elapsed:.2f}s — {row_count:,} rows"
                    )
                )
            except Exception as e:
                elapsed = time.monotonic() - start
                error_msg = f"{view_name} refresh failed after {elapsed:.2f}s: {e}"
                errors.append(error_msg)
                self.stdout.write(self.style.ERROR(f"   ✗ {error_msg}"))

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n── Refresh complete ({len(errors)} errors) ──\n"
            )
        )

        if errors:
            raise CommandError(
                f"Materialized view refresh completed with {len(errors)} error(s)."
            )

    def _refresh_view(self, view_name: str) -> None:
        """
        Execute REFRESH MATERIALIZED VIEW CONCURRENTLY.

        CONCURRENTLY: builds a new version of the view in parallel with
        existing reads. Readers are never blocked. Writers see the old
        version until the refresh completes, then atomically switch.

        Without CONCURRENTLY: acquires an exclusive lock for the full
        refresh duration — all reads block. At 10M rows this can be
        45+ seconds of downtime on the analytics endpoints.

        Note: view_name is not user-supplied — it comes from VIEWS dict
        which is hardcoded above. No SQL injection risk despite lack of
        parameterization (DDL statements cannot use %s placeholders).
        """
        sql = f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name}"
        with connection.cursor() as cursor:
            cursor.execute(sql)

    def _get_row_count(self, view_name: str) -> int:
        """Fast approximate row count using pg_class statistics."""
        sql = """
            SELECT reltuples::bigint
            FROM pg_class
            WHERE relname = %s
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [view_name])
            row = cursor.fetchone()
            return int(row[0]) if row else 0