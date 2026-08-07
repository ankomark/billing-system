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
    if not _postgres() or connection.connection is None:
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

    # Never open a connection merely to set a variable. During a database
    # outage that would add a failed connect — and a stack trace — to every
    # single request, including ones that would not otherwise touch the
    # database at all. When no connection is open yet, the connection_created
    # receiver below applies the scope the moment one is.
    if connection.connection is None:
        return

    value = "" if tenant_id is None else str(tenant_id)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config(%s, %s, %s)", [RLS_SETTING, value, local])


def apply_scope_to_new_connection(sender, connection, **kwargs):
    """
    Stamp the current operator onto a freshly opened connection.

    Wired to Django's connection_created signal. Without it, a request that
    opens its connection lazily — the common case — would run its first query
    before any scope was applied, and RLS would let that query see everything.
    """
    if connection.vendor != "postgresql":
        return
    tenant_id = get_current_tenant_id()
    value = "" if tenant_id is None else str(tenant_id)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config(%s, %s, false)", [RLS_SETTING, value])


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

    **Use it inside a transaction.** The clearing is done with
    ``set_config(..., local=true)``, and a LOCAL setting outside a transaction
    applies only to the statement that set it. A web request runs in autocommit
    — ATOMIC_REQUESTS is not on — so without one the scope is back in place
    before the query underneath runs, and you are silently scoped again:

        with transaction.atomic(), all_tenants():
            ...

    LOCAL rather than session-scoped is the right default: it reverts when the
    transaction ends, so an unscoped block cannot leak past itself onto a
    pooled connection that the next request will reuse.

    Pair it with ``Model.objects.all_tenants()``, which lifts the ORM filter.
    Each clears one of the two layers; neither is sufficient alone. See that
    method for the two bugs this has caused.
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
        """
        Lift *this* layer's filter. Almost never enough on its own.

        There are two layers keeping one operator's rows away from another:
        the ORM filter in get_queryset above, and row-level security in
        Postgres. This method removes the first. **RLS is untouched**, so on a
        connection scoped to an operator the database goes on filtering and the
        queryset still returns only that operator's rows — while reading, at
        the call site, exactly as though it returned everybody's.

        Nothing fails. No exception, no empty result, no warning. You get a
        smaller answer than you asked for and no indication that you did.

        For a genuinely cross-operator read, pair it with the context manager
        of the same name, which clears the database setting too:

            with all_tenants():                       # clears RLS
                rows = Thing.objects.all_tenants()    # clears the ORM filter

        and do that inside a transaction. The context manager clears the scope
        with ``set_config(..., local=true)``, and a LOCAL setting outside a
        transaction lasts only for the statement that set it — which, in a
        request running in autocommit, is over before your query runs:

            with transaction.atomic(), all_tenants():
                ...

        This has been got wrong twice, and both times the code read correctly
        and the failure was silent and remote from the cause:

        * ``RouterSerializer._check_address_is_free`` asked whether any *other*
          operator had already registered a public address. Scoped by RLS to
          the caller, it could only ever find the caller's own routers, so the
          answer was always no. Two operators could register one address, and
          the platform would then dial one operator's hardware holding the
          other's credentials.
        * ``services.tunnel.allocate_tunnel_ip`` picked the next free address
          on the management tunnel. A tunnel address belongs to one router on
          the whole platform, but scoped it only saw one operator's — so the
          second operator to add a router was handed an address the first
          already held. Their tunnel would never come up, and both
          configurations would look entirely correct.

        Uniqueness that spans operators, and any question of the form "does
        anyone else already have this", cannot be answered from inside a
        tenant's scope. If that is the question, use the context manager.
        """
        return super().get_queryset()
