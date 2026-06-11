"""
apps/logistics/management/commands/populate_snapshots.py

Idempotent snapshot population command.
Safe to run multiple times for the same date — upsert logic prevents duplicates.
"""

from __future__ import annotations

import traceback
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.logistics.analytics import (
    populate_carrier_snapshots,
    populate_global_snapshots,
    populate_route_snapshots,
)

ENTITY_TYPES = ["CARRIER", "ROUTE", "GLOBAL", "ALL"]


class Command(BaseCommand):
    help = (
        "Populate AnalyticsSnapshot table with pre-aggregated metrics. "
        "Idempotent — safe to run multiple times for the same date. "
        "Example: python manage.py populate_snapshots --date 2025-01-15 --entity-type CARRIER"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help=(
                "Date to populate snapshots for (YYYY-MM-DD). "
                "Defaults to yesterday."
            ),
        )
        parser.add_argument(
            "--entity-type",
            type=str,
            default="ALL",
            choices=ENTITY_TYPES,
            help=(
                "Entity type to populate. "
                "Choices: CARRIER, ROUTE, GLOBAL, ALL. "
                "Default: ALL."
            ),
        )

    def handle(self, *args, **options):
        # ── Resolve date ───────────────────────────────────────────────────────
        if options["date"]:
            try:
                snapshot_date = date.fromisoformat(options["date"])
            except ValueError:
                raise CommandError(
                    f"Invalid date format: {options['date']}. Use YYYY-MM-DD."
                )
        else:
            snapshot_date = date.today() - timedelta(days=1)

        entity_type = options["entity_type"]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n── Populating snapshots for {snapshot_date} "
                f"[entity_type={entity_type}] ──\n"
            )
        )

        total_upserted = 0
        errors = []

        # ── CARRIER ────────────────────────────────────────────────────────────
        if entity_type in ("CARRIER", "ALL"):
            self.stdout.write("── Populating CARRIER snapshots ──")
            try:
                count = populate_carrier_snapshots(snapshot_date)
                total_upserted += count
                self.stdout.write(
                    self.style.SUCCESS(f"   ✓ {count} CARRIER snapshot rows upserted")
                )
            except Exception as e:
                # One entity type failure does not abort the full run
                error_msg = f"CARRIER snapshot failed: {e}"
                errors.append(error_msg)
                self.stdout.write(self.style.ERROR(f"   ✗ {error_msg}"))
                if options.get("verbosity", 1) >= 2:
                    self.stderr.write(traceback.format_exc())

        # ── ROUTE ──────────────────────────────────────────────────────────────
        if entity_type in ("ROUTE", "ALL"):
            self.stdout.write("── Populating ROUTE snapshots ──")
            try:
                count = populate_route_snapshots(snapshot_date)
                total_upserted += count
                self.stdout.write(
                    self.style.SUCCESS(f"   ✓ {count} ROUTE snapshot rows upserted")
                )
            except Exception as e:
                error_msg = f"ROUTE snapshot failed: {e}"
                errors.append(error_msg)
                self.stdout.write(self.style.ERROR(f"   ✗ {error_msg}"))

        # ── GLOBAL ─────────────────────────────────────────────────────────────
        if entity_type in ("GLOBAL", "ALL"):
            self.stdout.write("── Populating GLOBAL snapshots ──")
            try:
                count = populate_global_snapshots(snapshot_date)
                total_upserted += count
                self.stdout.write(
                    self.style.SUCCESS(f"   ✓ {count} GLOBAL snapshot rows upserted")
                )
            except Exception as e:
                error_msg = f"GLOBAL snapshot failed: {e}"
                errors.append(error_msg)
                self.stdout.write(self.style.ERROR(f"   ✗ {error_msg}"))

        # ── Summary ────────────────────────────────────────────────────────────
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n── Complete: {total_upserted} rows upserted "
                f"({len(errors)} errors) ──\n"
            )
        )

        if errors:
            self.stdout.write(
                self.style.WARNING(
                    "Some entity types failed. See errors above. "
                    "Run with --verbosity 2 for full tracebacks."
                )
            )
            # Exit with non-zero code so CI/alerting can detect partial failure
            raise CommandError(
                f"Snapshot population completed with {len(errors)} error(s)."
            )