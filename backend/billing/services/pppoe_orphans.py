"""
PPP secrets with nobody behind them.

A PPPoE secret is a working username and password for unmetered internet. When
the customer holding one is deleted, the row goes and the secret stays -- so
whoever knows the credentials keeps dialling in, and nothing in the system has
any record that they exist.

Three were found live on 2026-09-14: `st.ambrose` on both routers and `enock`
on one, all enabled, all deleted through the customers page weeks earlier. The
hotspot side had been surviving the same gap on its orphan sweep, which removes
accounts with no customer behind them; there has never been a PPPoE
equivalent, which is why these lasted.

CustomerViewSet.perform_destroy now clears a subscriber off the routers before
deleting them, and that is the fix. This is the backstop for everything that
does not go through it: a row deleted in the admin, a router unreachable at the
moment of deletion, an operator editing the database directly.

Disabled first, removed later -- the same two-step the hotspot sweep uses, and
for the same reason. Disabling is reversible and deletion is not, so the gap
between them is the window in which a mistake can be noticed. A secret that
turns out to belong to somebody real is re-enabled with one click; one that has
been deleted is gone.
"""

import datetime as dt
import logging
import re

logger = logging.getLogger(__name__)

# Written into the secret's comment when it is first disabled, so the sweep
# can tell how long it has been waiting without keeping state of its own.
STAMP_RE = re.compile(r"\| orphaned (\d{4}-\d{2}-\d{2})")
STAMP_PREFIX = "AUTO | WIFI BILLING SYSTEM | orphaned"

# How long a disabled orphan waits before it is removed. Matches the hotspot
# sweep: long enough that a month-end reconciliation finds it, short enough
# that dead credentials do not accumulate.
PURGE_AFTER_DAYS = 30


def stamped_on(row):
    """The date this secret was first seen orphaned, or None."""
    match = STAMP_RE.search(str(row.get("comment") or ""))
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(1))
    except ValueError:
        return None


def known_usernames(tenant_id=None):
    """
    Every PPPoE username the database still accounts for.

    Across all operators unless narrowed, because a secret on a router is
    matched by name alone and two operators can both have a "john". Treating
    one operator's subscriber as another's orphan would disconnect somebody
    who is paying.
    """
    from billing.models import Customer

    qs = Customer.objects.all_tenants().exclude(pppoe_username="")
    if tenant_id is not None:
        qs = qs.filter(tenant_id=tenant_id)
    return {u.lower() for u in qs.values_list("pppoe_username", flat=True) if u}


def find_orphan_secrets(router, api, *, known=None):
    """
    Secrets on one router that no customer claims, as (row, stamped_on).

    Never touches a secret whose name the database knows, whatever state it is
    in -- a suspended or expired subscriber still owns their credentials.
    """
    known = known_usernames() if known is None else known
    found = []
    try:
        rows = list(api.path("ppp", "secret"))
    except Exception as exc:
        logger.warning(
            "[pppoe-orphans] could not read secrets on %s: %s", router, exc)
        return []

    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name or name.lower() in known:
            continue
        found.append((row, stamped_on(row)))
    return found


def sweep_orphan_secrets(router, api, *, apply=False, today=None,
                         purge_after=PURGE_AFTER_DAYS, known=None):
    """
    Disable an orphan, then remove it once it has waited long enough.

    Returns (disabled, purged).

    An enabled orphan is disabled and stamped with today's date. A stamped one
    past the waiting period is removed. A stamped one still inside it is left
    exactly where it is, which is the point of the stamp.
    """
    today = today or dt.date.today()
    disabled = purged = 0

    secrets = api.path("ppp", "secret")
    for row, stamp in find_orphan_secrets(router, api, known=known):
        name = row.get("name")

        if stamp is None:
            comment = str(row.get("comment") or "").strip()
            mark = f"{STAMP_PREFIX} {today.isoformat()}"
            new_comment = f"{comment} {mark}".strip() if comment else mark
            disabled += 1
            if not apply:
                continue
            try:
                # Any live session goes with it. Disabling a secret refuses the
                # next authentication and leaves an established session
                # running -- the same trap disable_customer_access documents
                # for PPPoE, where an expired subscriber stayed online for days.
                for a in list(api.path("ppp", "active")):
                    if str(a.get("name") or "") == str(name):
                        api.path("ppp", "active").remove(a[".id"])
                secrets.update(**{".id": row[".id"], "disabled": "yes",
                                  "comment": new_comment})
                logger.info(
                    "[pppoe-orphans] disabled %s on %s — no customer holds it; "
                    "it will be removed after %s days unless somebody claims it",
                    name, router.name, purge_after)
            except Exception as exc:
                logger.warning(
                    "[pppoe-orphans] could not disable %s on %s: %s",
                    name, router.name, exc)
            continue

        if (today - stamp).days < purge_after:
            continue

        purged += 1
        if not apply:
            continue
        try:
            secrets.remove(row[".id"])
            logger.info(
                "[pppoe-orphans] removed %s from %s — orphaned since %s",
                name, router.name, stamp.isoformat())
        except Exception as exc:
            logger.warning(
                "[pppoe-orphans] could not remove %s on %s: %s",
                name, router.name, exc)

    return disabled, purged


def sweep_orphan_secrets_everywhere(*, apply=False, routers=None, today=None,
                                    purge_after=PURGE_AFTER_DAYS):
    """
    Sweep an operator's estate. Unreachable routers are skipped, not failed.

    The known-usernames set is read once and shared, so a sweep of several
    routers cannot decide differently on each because somebody was created
    halfway through.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    known = known_usernames()
    disabled = purged = 0

    for router in (routers if routers is not None
                   else RouterDevice.objects.all_tenants().filter(
                       is_active=True)):
        api = safe_connect_router(router)
        if not api:
            logger.info("[pppoe-orphans] %s unreachable — skipped", router)
            continue
        d, p = sweep_orphan_secrets(
            router, api, apply=apply, today=today,
            purge_after=purge_after, known=known)
        disabled += d
        purged += p

    return disabled, purged
