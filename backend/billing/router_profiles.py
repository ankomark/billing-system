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

import logging

from librouteros import connect

logger = logging.getLogger(__name__)

# See ensure_hotspot_profile, which explains what this buys and what it costs.
# A constant rather than a literal because the value is a judgement about the
# devices these operators actually serve — phones that sleep — and not a
# RouterOS detail.
#
# `none`, after 2m and then 5m both failed.
#
# keepalive-timeout logs a subscriber out when their handset stops answering
# the router's ARP. That is a liveness check designed for equipment that is
# always awake, and a phone is not: 802.11 power save means a locked handset
# can go minutes without answering while its owner is sitting under the AP,
# holding it. Raising 2m to 5m only moved the threshold — with 5m live on
# every profile, skylink still logged 44 keepalive logouts in 47 minutes and
# skylink3 81, against 214 logins in the same window on a site with roughly
# 60 real sessions. Every subscriber was being reaped and silently re-admitted
# by their cookie several times an hour, which is precisely the "disconnected
# and reconnected after a few minutes" the operator's customers report.
#
# There is no correct number here. Any threshold short enough to reap a
# genuinely departed device is short enough to reap a sleeping one, because
# the two look identical over ARP.
#
# What ends a session instead, now that ARP does not:
#   * limit-uptime, written at grant time as the subscriber's remaining
#     wall-clock — enable_hotspot sets it on every user.
#   * disable_customer_access at expiry, which removes the user and drops the
#     session through the same call.
#   * the subscriber logging out, or the operator disconnecting them.
# None of those mistake a locked phone for a departed one.
#
# The cost is that a device which leaves without logging out keeps its session
# until one of the above fires, so it holds a shared-users slot in the
# meantime. On these packages shared-users is 2, so a household with one
# device away still has a free slot; and the slot is returned at expiry
# regardless. That is a far smaller harm than logging everybody out hourly.
HOTSPOT_KEEPALIVE = "none"


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

    `dns-server` is copied for the same reason and was missing from this list,
    which cost the first PPPoE subscriber a morning on 2026-09-01. A client
    with no resolver authenticates, takes an address, installs a working
    default route and cannot look anything up — the same "connected, no
    internet" this function already exists to prevent, one step further along.
    Nothing in the profile says it is wrong, and the router log says the login
    succeeded, so there is nothing to find from either end.
    """
    for p in profiles:
        if p.get("name") == "default":
            return {
                key: p[key]
                for key in ("local-address", "remote-address", "dns-server")
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

    wanted = {
        "rate-limit": _rate_limit(package),
        # How many devices one voucher may be used from at once.
        "shared-users": str(devices),
        # How long a handset may fail to answer the router's ARP before its
        # session is torn down.
        #
        # RouterOS defaults this to 2m, which is short enough that a phone in
        # power save with the screen off is logged out for sleeping. The
        # subscriber wakes it, the cookie logs them straight back in, and what
        # they experience is the connection dropping over and over — 13 such
        # logouts in a single log buffer on skylink when this was found, next
        # to complaints about being disconnected constantly.
        #
        # Set here rather than only on the routers, because this dict is also
        # what the repair path above compares against: a profile that drifts
        # is corrected, and a profile created for a new package is born with
        # it instead of with RouterOS's default.
        #
        # idle-timeout is deliberately not set alongside it. That one measures
        # traffic rather than reachability, and turning it on would disconnect
        # somebody who is connected and simply not using it.
        "keepalive-timeout": HOTSPOT_KEEPALIVE,
    }

    for p in profiles:
        if p.get("name") == profile_name:
            # Repaired, not merely reused — the same thing ensure_pppoe_profile
            # does, and for the same reason.
            #
            # This returned on sight of the name, so a profile already on the
            # router was never looked at again. The name carries the package id
            # and the device count, neither of which changes when an operator
            # edits a *speed* — so changing a package from 6M to 2M updated the
            # database, reported success on the dashboard, and left every
            # router still handing out 6M. Nothing anywhere said the two
            # disagreed, and the only way to land the new rate was to delete
            # the profile on each router by hand.
            #
            # Found while throttling every hotspot package to 2M for capacity
            # on 2026-09-05: the speed change would have been silently
            # cosmetic on every router in the estate.
            stale = {k: v for k, v in wanted.items() if p.get(k) != v}
            if stale:
                profiles.update(**{".id": p[".id"], **stale})
                logger.info(
                    "[hotspot] profile %s on %s updated: %s",
                    profile_name, router, stale,
                )
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
    profiles.add(name=profile_name, **wanted)

    return profile_name
