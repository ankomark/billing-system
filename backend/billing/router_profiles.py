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

def _pppoe_addresses(profiles):
    """
    Where PPPoE clients get their addresses from, as the operator set it up.

    RouterOS takes address assignment from the profile named on the *secret*,
    not from the PPPoE server's default-profile. A generated profile carrying
    only a rate limit therefore authenticates the client and gives it no IP:
    the session comes up, the credentials are correct, and there is no
    internet. That is a support call nobody can diagnose from the dashboard,
    because every record says the customer is provisioned.

    Copied from `default` rather than hardcoded. The pool and the router's own
    tunnel-side address differ per operator, and `default` is where the
    documented setup puts them — so an operator configures this once per
    router and every package profile follows.
    """
    for p in profiles:
        if p.get("name") == "default":
            return {
                key: p[key]
                for key in ("local-address", "remote-address")
                if p.get(key)
            }
    return {}


def ensure_pppoe_profile(router, package):
    """
    Create or reuse a PPPoE profile for a package.

    Repairs an existing one rather than trusting it. These are rebuilt from
    the package every time a subscriber is provisioned, and a profile made
    before the addresses were configured — or left behind by a package whose
    speed has since changed — is indistinguishable by name from a correct one.
    """

    api = connect_router(router)
    profiles = api.path("ppp", "profile")

    # ✅ Better readable profile name
    profile_name = f"PPPOE_PKG_{package.id}"

    wanted = {
        "rate-limit": _rate_limit(package),
        # One session per account. Without it a household shares one PPPoE
        # login across every device and every neighbour they lend it to.
        #
        # This is not the package's device limit, and must not be wired to it.
        # A PPPoE subscriber's devices sit behind their own router, sharing
        # this one session and the rate limit on it — which is exactly how the
        # speed a customer buys ends up shared across their household.
        "only-one": "yes",
        "comment": f"Auto: {package.name}",
    }
    wanted.update(_pppoe_addresses(profiles))

    for p in profiles:
        if p.get("name") == profile_name:
            stale = {k: v for k, v in wanted.items() if p.get(k) != v}
            if stale:
                profiles.update(**{".id": p[".id"], **stale})
            return profile_name

    profiles.add(name=profile_name, **wanted)

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

    # No comment. RouterOS has no comment property on
    # /ip/hotspot/user/profile, and rejects the whole request rather than
    # ignoring the extra field: "unknown parameter comment". So the profile is
    # never created, enable_hotspot raises, and the customer who has just paid
    # gets a 500 and no internet — every hotspot activation, on every router.
    #
    # /ppp/profile does accept it, which is why the PPPoE path above keeps
    # its comment and this one cannot. Verified against RouterOS 7.19.6 by
    # adding a profile to each: one succeeded, the other trapped.
    #
    # Nothing is lost. The name already carries what the comment said and
    # more — HOTSPOT_PKG_<package>_D<devices> identifies the package and the
    # device allowance, which is what makes these rebuildable.
    profiles.add(**{
        "name": profile_name,
        "rate-limit": _rate_limit(package),
        # How many devices one voucher may be used from at once.
        "shared-users": str(max(1, getattr(package, "max_devices", 1) or 1)),
    })

    return profile_name
