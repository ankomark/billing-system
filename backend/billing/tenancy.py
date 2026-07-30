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


RLS_SETTING = "app.current_tenant_id"


def _postgres():
    return connection.vendor == "postgresql"


def read_db_tenant():
    """Current value of the Postgres setting the RLS policies read."""
    if not _postgres():
        return None
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting(%s, true)", [RLS_SETTING])
        return cur.fetchone()[0]


def write_db_tenant(tenant_id, *, local=True):
    """
    Tell Postgres which operator this connection is acting for.

    `local=True` is transaction-scoped and reverts when the transaction ends —
    right for a task or a nested block. `local=False` is session-scoped and
    survives across transactions, which is what a web request needs, since a
    request is not one transaction.

    The session-scoped form is only safe because the middleware writes it at
    the start of *every* request. With CONN_MAX_AGE the connection is reused,
    and the hazard is a stale value from the previous request — overwriting it
    unconditionally before any query runs is what removes that hazard.
    """
    if not _postgres():
        return
    value = "" if tenant_id is None else str(tenant_id)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config(%s, %s, %s)", [RLS_SETTING, value, local])


@contextmanager
def tenant_context(tenant, *, set_db_session=True):
    """
    Run a block scoped to one operator.

    Required for anything without a request — Celery tasks, management
    commands, the M-Pesa callback — because no middleware runs there.

        with tenant_context(tenant):
            enable_customer_access(customer)

    Restores the previous database setting on exit, not just the ContextVar.
    Without that the two layers drift apart: the application would believe it
    had returned to unscoped while Postgres was still filtering to the operator
    from the block that just ended, and rows would go quietly missing.
    """
    tenant_id = getattr(tenant, "pk", tenant)
    token = set_current_tenant_id(tenant_id)
    touch_db = set_db_session and _postgres()
    previous = read_db_tenant() if touch_db else None
    try:
        if touch_db:
            write_db_tenant(tenant_id)
        yield
    finally:
        reset_current_tenant_id(token)
        if touch_db:
            write_db_tenant(previous or None)


@contextmanager
def all_tenants():
    """
    Deliberately unscoped. For platform dashboards and cross-operator sweeps.

    Explicit by design — an unscoped read should be visible at the call site,
    not something that happens because a filter was forgotten.

    Clears the database setting too. Clearing only the ContextVar would leave
    RLS still filtering, so a block that reads as cross-operator would silently
    return one operator's rows.
    """
    token = set_current_tenant_id(None)
    touch_db = _postgres()
    previous = read_db_tenant() if touch_db else None
    try:
        if touch_db:
            write_db_tenant(None)
        yield
    finally:
        reset_current_tenant_id(token)
        if touch_db:
            write_db_tenant(previous or None)


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
