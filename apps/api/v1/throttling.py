from __future__ import annotations

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AnonThrottle(AnonRateThrottle):
    scope = "anon"

    def get_rate(self) -> str:
        return getattr(settings, "THROTTLE_RATES", {}).get("anon", "10/minute")


class ShipmentListThrottle(UserRateThrottle):
    scope = "shipment_list"

    def get_rate(self) -> str:
        return getattr(settings, "THROTTLE_RATES", {}).get("shipment_list", "100/minute")


class AnalyticsThrottle(UserRateThrottle):
    scope = "analytics"

    def get_rate(self) -> str:
        return getattr(settings, "THROTTLE_RATES", {}).get("analytics", "30/minute")