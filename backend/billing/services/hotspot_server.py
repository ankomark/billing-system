"""
The hotspot server settings a router has to carry, and why.

These are not provisioned by anything. They are answered once, by hand, in the
MikroTik setup wizard when a site is built -- so they drift, they differ
between routers built by different people on different days, and nothing
notices. Every one of the three below was found wrong on live hardware on
2026-09-10, and each was costing customers their connection in a way that
looked like something else.

Kept as a check rather than a rewrite: this reports what disagrees and can
correct it on request. A hotspot server is the thing every subscriber's traffic
passes through, and quietly rewriting one because a sweep ran is not a trade
worth making.
"""
import logging

logger = logging.getLogger(__name__)

# What the hotspot server itself must say.
SERVER_WANTED = {
    # The hotspot must not allocate addresses.
    #
    # It was set to `default-dhcp` -- the same pool the DHCP server hands
    # leases from -- so the two competed for one 245-address range. On
    # 2026-09-10 /ip/pool/used read {'DHCP': 119, 'hotspot': 126} = 245 of 245
    # on skylink, and the router refused every new arrival with "failed to get
    # IP address for host <mac>/<ip>: pool empty". A device refused an address
    # gets no host entry, and a device with no host entry cannot be
    # authenticated by ANY method -- so it sat there showing "connected, no
    # internet" with no sign-in page, which is what the operator had been
    # chasing for days.
    #
    # `none` means the client keeps the address DHCP already gave it. One-to-one
    # NAT only matters for a client arriving with a foreign static address,
    # which on a hotspot of phones and televisions is nobody. Setting it
    # dropped not one of the 101 live sessions and took the refusals to zero.
    "address-pool": "none",

    # One address per device, not two.
    #
    # Belt and braces now that address-pool is none, and it matters if anyone
    # ever sets a pool again: at 2 the site is capped near half the pool
    # whatever the lease time is.
    "addresses-per-mac": "1",
}

# What the server's PROFILE must say.
PROFILE_WANTED = {
    # A device that has been paid for must be able to get on without a browser.
    #
    # login-by was `cookie,http-chap`. Both require the DEVICE to load the
    # portal and post credentials, which a television cannot do -- many have no
    # captive-portal browser at all. So a voucher bound to a TV's address
    # created the account, configured the router, and the set still said it had
    # no internet, for ever. Adding `mac` lets RouterOS admit any device whose
    # address matches a hotspot user, with no HTTP step; enable_hotspot already
    # creates those users with an empty password, which is what MAC
    # authentication expects.
    #
    # cookie and http-chap are kept, never replaced: a walk-up with no account
    # still needs the portal to buy one.
    "login-by": "mac,cookie,http-chap",
}


def _server_of(api):
    for s in api.path("ip", "hotspot"):
        return s
    return None


def check(api):
    """
    What disagrees, as {setting: (found, wanted)}. Empty means correct.

    Reads only. This is what the status command shows and what the fixer acts
    on, so there is one answer to "is this router right" rather than two.
    """
    drift = {}

    server = _server_of(api)
    if server is None:
        return {"hotspot": ("no hotspot server", "one")}

    for key, want in SERVER_WANTED.items():
        found = str(server.get(key))
        # RouterOS answers the absent pool as None, "none" or "" depending on
        # version and on whether it was ever set. All three mean the same
        # thing, and treating them as drift would rewrite a correct router.
        if key == "address-pool" and found.lower() in ("none", "", "false"):
            continue
        if found != want:
            drift[key] = (found, want)

    for p in api.path("ip", "hotspot", "profile"):
        if str(p.get("name")) != str(server.get("profile")):
            continue
        found = str(p.get("login-by"))
        if "mac" not in [m.strip() for m in found.split(",")]:
            drift["login-by"] = (found, PROFILE_WANTED["login-by"])

    return drift


def apply(api, drift):
    """Correct what check() found. Returns what was changed."""
    changed = {}
    server = _server_of(api)
    if server is None:
        return changed

    servers = api.path("ip", "hotspot")
    for key in ("address-pool", "addresses-per-mac"):
        if key in drift:
            servers.update(**{".id": server[".id"], key: drift[key][1]})
            changed[key] = drift[key]

    if "login-by" in drift:
        profiles = api.path("ip", "hotspot", "profile")
        for p in list(profiles):
            if str(p.get("name")) != str(server.get("profile")):
                continue
            found = [m.strip() for m in str(p.get("login-by")).split(",") if m.strip()]
            merged = ",".join(["mac"] + [m for m in found if m != "mac"])
            profiles.update(**{".id": p[".id"], "login-by": merged})
            changed["login-by"] = (drift["login-by"][0], merged)

    if changed:
        logger.info("[hotspot-server] corrected %s", sorted(changed))
    return changed
