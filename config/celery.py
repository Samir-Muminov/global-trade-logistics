"""
config/celery.py

Celery application for Global Trade & Logistics Analytics Platform.

Broker: Redis (already in requirements.txt from Phase 3.1).
Result backend: Redis — task results stored for 1 hour.
Serializer: JSON only — never pickle (arbitrary code execution risk).
Task routing: two queues — analytics (heavy) and notifications (lightweight).

Beat schedule replaces manual management command runs:
  populate_snapshots       — was: python manage.py populate_snapshots
  refresh_carrier_daily    — was: python manage.py refresh_materialized_views --view carrier_daily
  refresh_route_monthly    — was: python manage.py refresh_materialized_views --view route_monthly
  refresh_port_congestion  — was: python manage.py refresh_materialized_views --view port_congestion
  send_delay_notifications — new: no manual equivalent existed
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("global_trade")

app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()

app.conf.update(
    # ── Serialization ──────────────────────────────────────────────────────────
    # JSON only — pickle allows arbitrary code execution via crafted task args.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # ── Reliability ────────────────────────────────────────────────────────────
    # Acknowledge task only after it completes, not when it's received.
    # Without this: a worker crash mid-task = task lost silently.
    task_acks_late=True,

    # Reject tasks that were not acknowledged (worker died mid-execution).
    # Combined with acks_late: tasks are retried after worker crash.
    task_reject_on_worker_lost=True,

    # ── Result backend ─────────────────────────────────────────────────────────
    # Results stored for 1 hour — enough for monitoring dashboards to query.
    result_expires=3600,

    # ── Task routing ───────────────────────────────────────────────────────────
    # analytics queue: heavy aggregation tasks (snapshot population, MV refresh)
    # notifications queue: lightweight alert tasks
    task_routes={
        "apps.logistics.tasks.refresh_materialized_views_task": {"queue": "analytics"},
        "apps.logistics.tasks.populate_snapshots_task": {"queue": "analytics"},
        "apps.logistics.tasks.process_unmatched_webhook": {"queue": "analytics"},
        "apps.logistics.tasks.send_delay_notifications": {"queue": "notifications"},
    },

    # ── Beat schedule ──────────────────────────────────────────────────────────
    beat_schedule={
        # mv_carrier_daily_stats — every 15 minutes
        # Ops teams monitor carrier performance in near-real-time.
        "refresh-carrier-daily-stats": {
            "task": "apps.logistics.tasks.refresh_materialized_views_task",
            "schedule": crontab(minute="*/15"),
            "args": ["carrier_daily"],
        },

        # mv_port_congestion — every hour
        # Port data changes less frequently than carrier data.
        "refresh-port-congestion": {
            "task": "apps.logistics.tasks.refresh_materialized_views_task",
            "schedule": crontab(minute=0),
            "args": ["port_congestion"],
        },

        # mv_route_monthly_performance — nightly at 02:00 UTC
        # Monthly aggregation — no point refreshing more often.
        "refresh-route-monthly-performance": {
            "task": "apps.logistics.tasks.refresh_materialized_views_task",
            "schedule": crontab(hour=2, minute=0),
            "args": ["route_monthly"],
        },

        # populate_snapshots — daily at 01:00 UTC (after midnight data is stable)
        "populate-daily-snapshots": {
            "task": "apps.logistics.tasks.populate_snapshots_task",
            "schedule": crontab(hour=1, minute=0),
            "args": [],
            "kwargs": {"entity_type": "ALL"},
        },

        # delay notifications — every 30 minutes
        "send-delay-notifications": {
            "task": "apps.logistics.tasks.send_delay_notifications",
            "schedule": crontab(minute="*/30"),
            "args": [],
        },
    },
)