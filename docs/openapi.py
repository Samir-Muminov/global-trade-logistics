"""
docs/openapi.py

drf-spectacular configuration for OpenAPI schema generation.

This module provides:
- SPECTACULAR_SETTINGS dict (imported into settings/base.py)
- Custom preprocessing hooks for tagging endpoints
- Example schemas for request/response bodies

Serves at:
  /api/schema/  — raw OpenAPI 3.0 JSON
  /api/docs/    — Swagger UI (interactive)
"""

from __future__ import annotations


# ── SPECTACULAR SETTINGS ────────────────────────────────────────────────────────
# Import this dict into config/settings/base.py:
#   from docs.openapi import SPECTACULAR_SETTINGS

SPECTACULAR_SETTINGS = {
    "TITLE": "Global Trade & Logistics Analytics Platform API",
    "DESCRIPTION": (
        "Production REST API for global trade logistics: shipment booking, "
        "tracking, cargo management, carrier analytics, and webhook integrations.\n\n"
        "## Authentication\n"
        "All endpoints (except `/health/` and `/webhooks/`) require a JWT "
        "Bearer token. Obtain a token via `POST /api/v1/auth/token/`.\n\n"
        "## Idempotency\n"
        "All POST endpoints that create resources accept an `idempotency_key` "
        "(UUID). Repeating a request with the same key returns the original "
        "response instead of creating a duplicate.\n\n"
        "## Rate Limits\n"
        "- Shipment list/detail: 100/minute\n"
        "- Analytics endpoints: 30/minute\n"
        "- Auth endpoints: 5/minute\n"
        "- Anonymous requests: 10/minute (most endpoints require auth)"
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1/",

    # ── Swagger UI settings ───────────────────────────────────────────────────
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": False,
        "filter": True,
    },

    # ── Security scheme ───────────────────────────────────────────────────────
    "SECURITY": [{"BearerAuth": []}],
    "COMPONENT_SPLIT_REQUEST": True,

    # ── Tags — group endpoints logically in Swagger UI ───────────────────────
    "TAGS": [
        {
            "name": "Auth",
            "description": (
                "User registration, JWT login, email verification, "
                "password reset. See apps.users.urls."
            ),
        },
        {
            "name": "Shipments",
            "description": (
                "Shipment booking, status transitions, cargo management, "
                "and tracking events."
            ),
        },
        {
            "name": "Analytics",
            "description": (
                "Carrier leaderboards, dashboard summaries, and volume trends. "
                "Backed by materialized views for sub-millisecond reads."
            ),
        },
        {
            "name": "Webhooks",
            "description": (
                "Carrier system integrations via HMAC-signed webhooks. "
                "Not authenticated via JWT — uses per-carrier shared secrets."
            ),
        },
        {
            "name": "Health",
            "description": "Infrastructure health checks. No authentication required.",
        },
    ],

    # ── Postprocessing ────────────────────────────────────────────────────────
    "POSTPROCESSING_HOOKS": [
        "docs.openapi.add_bearer_auth_scheme",
    ],

    # ── Examples ──────────────────────────────────────────────────────────────
    "EXTENSIONS_INFO": {
        "x-logo": {
            "url": "https://example.com/logo.png",
            "altText": "Global Trade Logistics Platform",
        }
    },
}


# ── POSTPROCESSING HOOKS ────────────────────────────────────────────────────────


def add_bearer_auth_scheme(result: dict, generator, request, public) -> dict:
    """
    Adds the BearerAuth security scheme definition to the OpenAPI schema.

    drf-spectacular's SECURITY setting references "BearerAuth" but does not
    auto-generate the securitySchemes definition for JWT — this hook adds it.

    Without this hook: Swagger UI shows "Authorize" button but it does nothing
    because the scheme is undefined. With this hook: clicking "Authorize"
    shows a field to paste the JWT access token, which is then sent as
    `Authorization: Bearer <token>` on all subsequent requests in the UI.
    """
    result.setdefault("components", {})
    result["components"].setdefault("securitySchemes", {})
    result["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "JWT access token obtained from POST /api/v1/auth/token/. "
            "Token expires in 15 minutes — use the refresh token to obtain "
            "a new access token via POST /api/v1/auth/token/refresh/."
        ),
    }
    return result


# ── EXAMPLE SCHEMAS — used with @extend_schema decorators ───────────────────────
# These are reference examples for documentation purposes.
# To apply: import and use as `examples=[...]` in @extend_schema on views.

SHIPMENT_CREATE_EXAMPLE = {
    "summary": "Book a new shipment",
    "value": {
        "shipper_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "consignee_id": "4fb96g75-6828-5673-c4gd-3d074g77bgb7",
        "carrier_id": "5gc07h86-7939-6784-d5he-4e185h88chc8",
        "route_id": "6hd18i97-8a4a-7895-e6if-5f296i99did9",
        "origin_port_id": "7ie29j08-9b5b-89a6-f7jg-6g3a7j0ajeja",
        "destination_port_id": "8jf3ak19-ac6c-9ab7-g8kh-7h4b8k1bkfkb",
        "departure_date": "2026-08-15T10:00:00Z",
        "estimated_arrival": "2026-08-29T14:00:00Z",
        "declared_value": "125000.00",
        "currency_code": "USD",
        "incoterms": "FOB",
        "hs_codes": ["8471.30"],
        "idempotency_key": "9kg4bl2a-bd7d-ab8c-h9li-8i5c9l2clglc",
    },
}

SHIPMENT_STATUS_UPDATE_EXAMPLE = {
    "summary": "Mark shipment as departed",
    "value": {
        "new_status": "IN_TRANSIT",
        "note": "Vessel departed Shanghai on schedule.",
        "event_location": "Port of Shanghai, CN",
    },
}

CARGO_CREATE_EXAMPLE = {
    "summary": "Add a containerized cargo item",
    "value": {
        "cargo_type": "CONTAINER",
        "description": "Electronic components — LCD panels",
        "container_number": "MSCU1234567",
        "gross_weight_kg": "18500.000",
        "net_weight_kg": "17200.000",
        "volume_cbm": "58.5000",
        "package_count": 240,
        "package_type": "CT",
        "is_hazmat": False,
    },
}

TRACKING_EVENT_CREATE_EXAMPLE = {
    "summary": "Record vessel departure",
    "value": {
        "event_type": "DEPARTURE",
        "event_time": "2026-08-15T11:30:00Z",
        "port_id": "7ie29j08-9b5b-89a6-f7jg-6g3a7j0ajeja",
        "location_description": "Berth 12, Yangshan Deep Water Port",
        "description": "Vessel MSC OSCAR departed for Los Angeles.",
        "is_exception": False,
    },
}

WEBHOOK_PAYLOAD_EXAMPLE = {
    "summary": "Carrier webhook: vessel departed",
    "value": {
        "webhook_id": "evt_8f3a2b1c9d4e",
        "event_type": "vessel.departed",
        "shipment_reference": "GT-A3F8C201",
        "event_timestamp": "2026-08-15T11:30:00Z",
        "payload": {
            "vessel_name": "MSC OSCAR",
            "voyage_number": "045E",
            "port_unlocode": "CNSHA",
        },
    },
}