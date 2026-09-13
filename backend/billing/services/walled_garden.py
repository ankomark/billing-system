"""
Keep the walled garden from handing an unauthorised device working DNS.

A captive portal works by being the only thing a device can reach until it
logs in. The redirect that puts the signup page in front of somebody depends
on the router answering their DNS and intercepting their HTTP -- so anything
that lets an unauthorised device resolve names for itself defeats it.

Both routers were allowing exactly that: walled-garden rules accepting tcp/443
to 1.1.1.1, 1.0.0.1, 8.8.8.8 and 8.8.4.4, which is DNS-over-HTTPS to Cloudflare
and Google. They carry an "AUTO | WIFI BILLING SYSTEM" comment and nothing in
this repository creates them, so they are left over from a version that did.

What it does to a customer is precisely the complaint. Their phone resolves
names perfectly well over DoH, so the operating system does not conclude it is
behind a captive portal and never offers the sign-in prompt; meanwhile every
actual connection is dropped because the device is not authorised. The result
is "connected, no internet", with no page, no message, and nothing to tap. A
subscriber whose bundle has run out is in the same position: the system does
everything right -- suspends them, removes the account, ends the session -- and
the phone still will not show them where to buy another.

Removing the rules is the whole fix. DoH then fails, the phone falls back to
the DNS the router hands out, the interception works, and the portal appears.
Authorised subscribers are unaffected: the walled garden governs only devices
that have not logged in, so a paying customer's DoH keeps working exactly as
before.

Kept narrow on purpose. Only entries pointing at a known public resolver, and
only on the ports name resolution actually uses, are touched. An operator's own
walled-garden rules -- a payment provider, a messaging app, the portal itself
-- are none of this function's business.
"""

import logging

logger = logging.getLogger(__name__)

# The resolvers a phone reaches for on its own. Not a blocklist of the whole
# internet: the point is the handful of addresses devices are preconfigured to
# use, which is what makes DoH work without the user ever choosing it.
PUBLIC_RESOLVERS = {
    "1.1.1.1", "1.0.0.1",                 # Cloudflare
    "8.8.8.8", "8.8.4.4",                 # Google
    "9.9.9.9", "149.112.112.112",         # Quad9
    "208.67.222.222", "208.67.220.220",   # OpenDNS
    "94.140.14.14", "94.140.15.15",       # AdGuard
}

# 443 is DNS-over-HTTPS, 853 is DNS-over-TLS -- what Android calls Private DNS.
# Plain 53 is deliberately absent: the hotspot answers that itself, and it is
# the fallback the device needs once the encrypted paths stop working.
RESOLVER_PORTS = {"443", "853"}


def _is_resolver_bypass(entry):
    """
    Does this rule let an unauthorised device talk to a public resolver?

    Both halves must match. An entry for one of these addresses with no port is
    something else -- 1.1.1.1 is also a general-purpose host -- and an entry on
    443 to anywhere else is an ordinary allowed site.
    """
    address = str(entry.get("dst-address") or "").strip()
    port = str(entry.get("dst-port") or "").strip()
    if address not in PUBLIC_RESOLVERS:
        return False
    return port in RESOLVER_PORTS


def find_resolver_bypasses(api):
    """
    The offending entries, as (path, entry) so the caller can remove them.

    Both walled-garden lists are searched. RouterOS keeps hostname rules in
    `walled-garden` and address rules in `walled-garden ip`, and these were
    present in both -- removing one and not the other leaves the bypass open.
    """
    found = []
    for path in (("ip", "hotspot", "walled-garden"),
                 ("ip", "hotspot", "walled-garden", "ip")):
        try:
            for entry in list(api.path(*path)):
                if _is_resolver_bypass(entry):
                    found.append((path, entry))
        except Exception as exc:
            logger.warning(
                "[walled-garden] could not read %s: %s", "/".join(path), exc)
    return found


def close_resolver_bypasses(router, api, *, apply=True):
    """
    Remove the rules that let an unauthorised device resolve names itself.

    Returns how many were removed (or would be).

    Removed rather than disabled. This is the state the estate should be in,
    and it is re-asserted on a schedule -- a disabled rule is one careless
    click from being a live one again, and leaves somebody reading the router
    wondering whether it is meant to be there.
    """
    removed = 0

    for path, entry in find_resolver_bypasses(api):
        where = "/".join(path)
        if not apply:
            removed += 1
            logger.info(
                "[walled-garden] would remove %s %s:%s on %s (%s)",
                where, entry.get("dst-address"), entry.get("dst-port"),
                router.name, entry.get("comment") or "no comment")
            continue
        try:
            api.path(*path).remove(entry[".id"])
            removed += 1
            logger.info(
                "[walled-garden] removed %s %s:%s on %s — an unauthorised "
                "device can no longer resolve names for itself, so the portal "
                "shows instead of a page that never loads",
                where, entry.get("dst-address"), entry.get("dst-port"),
                router.name)
        except Exception as exc:
            logger.warning(
                "[walled-garden] could not remove %s %s on %s: %s",
                where, entry.get("dst-address"), router.name, exc)

    return removed


def close_resolver_bypasses_everywhere(*, apply=True, routers=None):
    """
    Assert it across an operator's estate. Unreachable routers are skipped.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    total = 0
    for router in (routers if routers is not None
                   else RouterDevice.objects.all_tenants().filter(
                       is_active=True)):
        api = safe_connect_router(router)
        if not api:
            logger.info("[walled-garden] %s unreachable — skipped", router)
            continue
        total += close_resolver_bypasses(router, api, apply=apply)

    return total
