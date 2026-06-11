"""
apps/api/v1/permissions.py
 
Global Trade & Logistics Analytics Platform — Phase 3: API Layer
Object-level permissions. IsAuthenticated alone is never sufficient.
"""
 
from __future__ import annotations
 
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView
 
from apps.logistics.models import Shipment
 
 
class IsShipmentOwnerOrStaff(BasePermission):
    """
    Object-level permission: allows access only if the requesting user's
    company is the shipper or consignee on the shipment, or the user is staff.
 
    IDOR RISK THIS CLOSES:
    Without this check, any authenticated user can request GET /api/v1/shipments/{uuid}/
    for any UUID. Since we use UUID PKs, brute-force is impractical — but a
    compromised account or an insider with a leaked shipment ID would have full
    read access to any shipment in the system. This check enforces data tenancy
    at the object level, not just the queryset level.
 
    Assumption: request.user has a `.company` attribute linking to a Company instance.
    This requires a UserProfile or extended User model (out of scope for Phase 3;
    documented as a dependency).
 
    Checked at view level in has_object_permission(), NOT in has_permission().
    has_permission() only validates authentication; object-level is a separate gate.
    """
 
    def has_permission(self, request: Request, view: APIView) -> bool:
        # Gate 1: must be authenticated at all
        return bool(request.user and request.user.is_authenticated)
 
    def has_object_permission(self, request: Request, view: APIView, obj: Shipment) -> bool:
        # Staff bypass: internal ops team has full read access
        if request.user.is_staff:
            return True
 
        # Object-level: user's company must be party to this shipment
        user_company_id = getattr(getattr(request.user, "company", None), "id", None)
        if user_company_id is None:
            return False
 
        return (
            obj.shipper_id == user_company_id
            or obj.consignee_id == user_company_id
        )
 
 
class IsCarrierActive(BasePermission):
    """
    Denies access if the requesting user's associated carrier is suspended.
 
    Checked at VIEW level (has_permission), not serializer level.
    Rationale: serializer validation runs after the queryset is already fetched.
    Checking carrier status in the serializer means DB work was done before
    the access was denied — wasteful and incorrect ordering of gates.
    View-level check short-circuits before any ORM call.
 
    Assumption: request.user has a `.carrier` attribute (nullable).
    Users without a carrier (shippers, staff) pass this check automatically.
    """
 
    def has_permission(self, request: Request, view: APIView) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False
 
        carrier = getattr(request.user, "carrier", None)
        if carrier is None:
            # Non-carrier users (shippers, staff) are not blocked by this permission
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
 