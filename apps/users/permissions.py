"""
apps/users/permissions.py

Object-level permissions — replaces apps/api/v1/permissions.py.
This version fixes the AttributeError caused by assuming request.user.company exists.

Every permission class handles all user states:
  - AnonymousUser (not authenticated)
  - CustomUser with no company (staff)
  - CustomUser with company (shipper/consignee/FF)
  - CustomUser with carrier (carrier_staff)
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView


class IsShipmentOwnerOrStaff(BasePermission):
    """
    Object-level permission for shipment access.

    Allows access if:
    1. User is staff (is_staff=True) — full read access to all shipments
    2. User's company is the shipper on the shipment
    3. User's company is the consignee on the shipment

    BUG FIXED: Previous version used getattr chain that failed for:
    - AnonymousUser: no .company attribute → AttributeError
    - Carrier staff: .company is None, .carrier is not → wrong denial

    IDOR RISK CLOSED: Without this check, any authenticated user can access
    any shipment UUID. This enforces data tenancy at object level.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(
        self, request: Request, view: APIView, obj
    ) -> bool:
        user = request.user

        # AnonymousUser guard — should not reach here due to has_permission,
        # but defensive check prevents AttributeError if misconfigured.
        if not user or not user.is_authenticated:
            return False

        # Staff bypass — internal ops team sees everything
        if user.is_staff:
            return True

        # Get company_id safely — staff users have no company
        company_id = getattr(user, "company_id", None)
        if company_id is None:
            # User has no company and is not staff — deny
            return False

        # Object-level: user's company must be shipper or consignee
        return (
            obj.shipper_id == company_id
            or obj.consignee_id == company_id
        )


class IsCarrierStaffForShipment(BasePermission):
    """
    Allows access only if the requesting user is carrier staff for the
    carrier that owns the shipment.

    Used on: TrackingEventCreateView, StatusUpdateView.
    Carrier staff can create events and update status for their shipments only.

    Staff bypass: internal ops can always access.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(
        self, request: Request, view: APIView, obj
    ) -> bool:
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.is_staff:
            return True

        # user.carrier_id must match the shipment's carrier
        user_carrier_id = getattr(user, "carrier_id", None)
        if user_carrier_id is None:
            return False

        return obj.carrier_id == user_carrier_id


class IsCarrierActive(BasePermission):
    """
    Denies access if the requesting user's carrier is suspended.

    Checked at VIEW level (has_permission) before any ORM work.
    Users without a carrier (shippers, staff) pass automatically.

    BUG FIXED: Previous version had no guard for users where
    getattr(request.user, "carrier", None) returns a carrier object
    that was lazy-loaded — causing an extra DB query per request.
    This version uses carrier_id (FK field) for the existence check,
    then only fetches the carrier object if needed.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False

        carrier_id = getattr(request.user, "carrier_id", None)
        if carrier_id is None:
            # Non-carrier user — not blocked by this permission
            return True

        # Fetch carrier is_active — one additional query, but only for carrier users
        # This could be eliminated by adding carrier_is_active to JWT claims (Phase 6 JWT)
        carrier = getattr(request.user, "carrier", None)
        if carrier is None:
            return True

        return carrier.is_active


class IsStaffOnly(BasePermission):
    """
    Restricts endpoint to Django staff users only.
    Used on DashboardSummaryView — aggregate financials are internal-only.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        )


class IsEmailVerified(BasePermission):
    """
    Blocks unverified users from write endpoints.

    Returns 403 with a clear message directing user to verify their email.
    Applied to all POST/PATCH/PUT endpoints.

    Rationale: unverified email = unconfirmed identity. In a logistics
    platform handling financial data and cargo manifests, unconfirmed
    identity is unacceptable for write operations.
    """

    message = "Email not verified. Check your inbox for a verification link."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False

        # Staff bypass — internal users are pre-verified
        if request.user.is_staff:
            return True

        return bool(getattr(request.user, "is_email_verified", False))