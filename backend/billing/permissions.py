"""
Who may call what.

Two populations:

- **Platform staff** run the platform. Their User.tenant is NULL, so their
  queries run unscoped and they see every operator.
- **Operator staff** run one WiFi business. TenantMiddleware scopes their
  queries to their own tenant.

Platform staff deliberately satisfy the operator permissions too, so support
can act on an operator's behalf.

This replaces a mix of a role-based IsAdmin and DRF's IsAdminUser. The latter
checks only `is_staff`, which is unrelated to these roles: it let any Django
staff account — including a customer — through fourteen admin endpoints, while
locking out operator admins who happened not to have the flag set.
"""

from rest_framework.permissions import BasePermission

from .models import User


def _role(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    return user.role


class IsPlatformStaff(BasePermission):
    """Platform owner or platform staff. Sees and acts across every operator."""

    message = "This endpoint is restricted to platform staff."

    def has_permission(self, request, view):
        return _role(request) in User.PLATFORM_ROLES


class IsPlatformOwner(BasePermission):
    """The platform owner alone. For irreversible platform-level actions."""

    message = "This endpoint is restricted to the platform owner."

    def has_permission(self, request, view):
        return _role(request) == User.PLATFORM_OWNER


class IsTenantAdmin(BasePermission):
    """
    Administers one operator: customers, packages, routers, settings, billing.
    Platform staff are included so support can act on an operator's behalf.
    """

    message = "This endpoint is restricted to operator administrators."

    def has_permission(self, request, view):
        return _role(request) in (User.TENANT_ADMIN, *User.PLATFORM_ROLES)


class IsTenantMember(BasePermission):
    """Any operator staff member — admin or ordinary staff."""

    message = "This endpoint is restricted to operator staff."

    def has_permission(self, request, view):
        return _role(request) in (
            User.TENANT_ADMIN, User.TENANT_STAFF, *User.PLATFORM_ROLES,
        )


class IsCustomer(BasePermission):
    """An end subscriber, acting on their own records."""

    def has_permission(self, request, view):
        return _role(request) == User.CUSTOMER


# ─── Backwards-compatible aliases ────────────────────────────────────────────
# Kept so anything still importing the old names keeps working. Prefer the
# names above in new code.
IsAdmin = IsTenantAdmin
IsStaffOrAdmin = IsTenantMember
