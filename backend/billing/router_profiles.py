"""
Router profiles.

A note on attribute names, because it cost every limit in this file.

RouterOS attributes are hyphenated — rate-limit, shared-users, only-one — and
librouteros sends keyword arguments through verbatim, with no translation. A
Python keyword cannot contain a hyphen, so `rate_limit=...` puts the literal
word "rate_limit" on the wire and RouterOS does not recognise it.

The effect was that none of it applied. Speed tiers were unlimited, a PPPoE
account could be logged in from anywhere as many times as you liked, a hotspot
voucher had no device limit, and no session ever timed out at the router. Every
one of those is a paid boundary that was not there.

So: hyphenated keys, passed as a dict. There is a test that reads the exact
keys these calls send, because the failure is silent — the router simply does
not do what it was never asked to do.
"""

from librouteros import connect


# ======================================================
# ROUTER CONNECTION
# ======================================================

def connect_router(router):
    return connect(
        username=router.username,
        password=router.password,
        host=router.ip_address,
        port=router.api_port,
    )


def _rate_limit(package):
    """
    MikroTik format: upload/download
    Example: 5Mbps up, 10Mbps down → 5M/10M
    """
    return f"{package.upload_speed}M/{package.download_speed}M"


# ======================================================
# PPPoE PROFILES
# ======================================================

def ensure_pppoe_profile(router, package):
    """
    Create or reuse a PPPoE profile for a package
    """

    api = connect_router(router)
    profiles = api.path("ppp", "profile")

    # ✅ Better readable profile name
    profile_name = f"PPPOE_PKG_{package.id}"

    for p in profiles:
        if p.get("name") == profile_name:
            return profile_name

    profiles.add(**{
        "name": profile_name,
        "rate-limit": _rate_limit(package),
        # One session per account. Without it a household shares one PPPoE
        # login across every device and every neighbour they lend it to.
        "only-one": "yes",
        "comment": f"Auto: {package.name}",
    })

    return profile_name


# ======================================================
# HOTSPOT PROFILES
# ======================================================

def ensure_hotspot_profile(router, package):
    """
    Create or reuse a Hotspot user profile for a package
    """

    api = connect_router(router)
    profiles = api.path("ip", "hotspot", "user", "profile")

    # The device count is baked into the profile, so it is part of the name.
    # Otherwise a package edited from one device to three keeps the profile it
    # already had, and the new limit never reaches the router.
    devices = max(1, getattr(package, "max_devices", 1) or 1)
    profile_name = f"HOTSPOT_PKG_{package.id}_D{devices}"

    for p in profiles:
        if p.get("name") == profile_name:
            return profile_name

    profiles.add(**{
        "name": profile_name,
        "rate-limit": _rate_limit(package),
        # How many devices one voucher may be used from at once.
        "shared-users": str(max(1, getattr(package, "max_devices", 1) or 1)),
        "comment": f"Auto: {package.name}",
    })

    return profile_name
