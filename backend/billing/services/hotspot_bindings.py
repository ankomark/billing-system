"""
Devices that walk past the captive portal without an account at all.

`/ip/hotspot/ip-binding` with `type=bypassed` is a MAC the hotspot waves
through: no login, no user profile, so no `limit-uptime`, no
`limit-bytes-total`, no rate limit, and nothing in this system has ever read
that table. Every other way onto the network starts with a row in
`/ip/hotspot/user` -- which is what `hotspot_logins` and `uncovered_logins`
both sweep -- so a binding is invisible to both of them by construction.

Found on skylink3 on 2026-09-23 while looking for something else:

    ip hotspot ip-binding  6C:02:E0:08:B5:23  type=bypassed  "admin laptop"

It had been there long enough that nobody remembered writing it, and no report
anywhere in the platform would ever have mentioned it.

WHY THIS ONE REPORTS RATHER THAN DISABLES

The other sweeps act on sight because an account they find has no legitimate
explanation: provisioning names every account it writes after a device MAC, so
anything else was not sold. A binding is different. Bypassing a till, a
payment terminal, a CCTV recorder or a printer is a reasonable thing for an
operator to have done deliberately, and those devices have no browser to sign
in with -- switching one off on a schedule would take out the till on a Friday
evening with no warning and no obvious cause.

So this reports by default, and an operator marks the deliberate ones:

    /ip hotspot ip-binding set [find mac-address=AA:BB:CC:DD:EE:FF] \\
        comment="KEEP | till printer"

A binding carrying KEEP is left alone and stops being reported. Once the
legitimate ones are marked, `close_bypassing_bindings(apply=True)` in the
hourly task closes the rest, and that is a one-line change in router_tasks.

Disables. Does not remove -- same reason as everywhere else here: the row is
the only record of what the device was, and `disabled=no` on the same `.id`
puts it back.
"""

import logging

logger = logging.getLogger(__name__)

# What an operator writes on a binding they meant to have. Checked case
# insensitively and anywhere in the comment, so "KEEP | till" and
# "till printer - keep" both count: the point is to be easy to mark, not to
# police the wording.
KEEP_MARK = "KEEP"

# What this writes when it does close one, so the next reader knows which
# sweep did it and when. Same shape as PROVISIONED_BY_US elsewhere.
CLOSED_MARK = "AUTO | bypass closed "


def _is_disabled(row):
    return str(row.get("disabled")).lower() in ("true", "yes")


def _kept(row):
    return KEEP_MARK.lower() in str(row.get("comment") or "").lower()


def find_bypassing_bindings(api):
    """
    Enabled `type=bypassed` bindings that no operator has marked deliberate.

    Reads only, so the status command and the fixer agree about what is there.
    """
    found = []
    for row in api.path("ip", "hotspot", "ip-binding"):
        if str(row.get("type")) != "bypassed":
            continue
        if _is_disabled(row) or _kept(row):
            continue
        found.append(row)
    return found


def close_bypassing_bindings(router, api, *, apply=False):
    """
    Report -- or, with apply, disable -- the bindings found on one router.

    Returns how many were found, not how many were closed: a report run and a
    closing run describe the same leak, and the caller already knows which one
    it asked for.
    """
    rows = find_bypassing_bindings(api)
    if not rows:
        return 0

    for row in rows:
        logger.warning(
            "[bindings] %s lets %s past the portal with no account (%s)%s",
            router, row.get("mac-address") or row.get("address"),
            row.get("comment") or "no comment",
            "" if apply else " — reporting only, mark it KEEP if deliberate")

    if not apply:
        return len(rows)

    import datetime as dt

    today = dt.date.today().isoformat()
    bindings = api.path("ip", "hotspot", "ip-binding")
    for row in rows:
        try:
            bindings.update(**{
                ".id": row[".id"],
                "disabled": "yes",
                "comment": f"{CLOSED_MARK}{today}",
            })
        except Exception:                       # pragma: no cover - transport
            logger.warning("[bindings] %s: could not disable %s",
                           router, row.get("mac-address"), exc_info=True)

    return len(rows)


def close_bypassing_bindings_everywhere(*, apply=False, routers=None):
    """
    The same across an operator's estate, one router at a time.

    An unreachable router is skipped rather than failing the sweep: it is
    serving whatever it had when it went away, and it will be read on the run
    after it comes back.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    if routers is None:
        routers = RouterDevice.objects.all_tenants().filter(is_active=True)

    total = 0
    for router in routers:
        api = safe_connect_router(router)
        if not api:
            logger.info("[bindings] %s unreachable — skipped", router)
            continue
        total += close_bypassing_bindings(router, api, apply=apply)

    return total
