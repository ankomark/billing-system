"""
Tenant scoping.

Three layers, deepest last:

1. `TenantManager` filters every query by the tenant in context, so the safe
   path is the default and crossing tenants requires typing `.all_tenants()`.
2. Middleware sets that context from the authenticated user on each request.
3. Postgres RLS (migration 0031) refuses cross-tenant rows even when the
   application layer is wrong.

The context is a ContextVar rather than thread-local storage: it is correct
under ASGI and inside async views, where one thread serves many requests.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connection, models

# None means "unscoped". Only platform staff and background sweeps that
# deliberately iterate every operator should ever run unscoped.
_current_tenant_id: ContextVar[int | None] = ContextVar(
    "current_tenant_id", default=None
)


def get_current_tenant_id():
    return _current_tenant_id.get()


def set_current_tenant_id(tenant_id):
    """Returns the token needed to restore the previous value."""
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant_id(token):
    _current_tenant_id.reset(token)


@contextmanager
def tenant_context(tenant, *, set_db_session=True):
    """
    Run a block scoped to one operator.

    Required for anything without a request — Celery tasks, management
    commands, the M-Pesa callback — because no middleware runs there.

        with tenant_context(tenant):
            enable_customer_access(customer)

    `set_db_session` also sets the Postgres session variable that RLS policies
    read. It uses `set_config(..., true)`, which is transaction-local: with
    CONN_MAX_AGE > 0 the connection is reused across requests, and a plain SET
    would leak one operator's context into the next request.
    """
    tenant_id = getattr(tenant, "pk", tenant)
    token = set_current_tenant_id(tenant_id)
    try:
        if set_db_session and connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.current_tenant_id', %s, true)",
                    [str(tenant_id) if tenant_id is not None else ""],
                )
        yield
    finally:
        reset_current_tenant_id(token)


@contextmanager
def all_tenants():
    """
    Deliberately unscoped. For platform dashboards and cross-operator sweeps.

    Explicit by design — an unscoped read should be visible at the call site,
    not something that happens because a filter was forgotten.
    """
    token = set_current_tenant_id(None)
    try:
        yield
    finally:
        reset_current_tenant_id(token)


class TenantManager(models.Manager):
    """
    Applies the tenant filter automatically.

    When no tenant is in context the queryset is unfiltered. That is what lets
    platform staff and cross-operator sweeps work, and is why the middleware
    must set the context on every authenticated request.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            return qs
        return qs.filter(tenant_id=tenant_id)

    def all_tenants(self):
        """Every row, regardless of context. Say it out loud."""
        return super().get_queryset()
