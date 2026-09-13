"""
Make the hotspot session follow the DHCP lease, on the router, as it happens.

A hotspot session is keyed by MAC *and* address together. When a device's
address changes hands the authorisation stays behind on the old one, and the
device's traffic arrives from the new one, unauthorised: "connected, no
internet" while holding a perfectly good voucher. Nothing on the router undoes
this. `keepalive-timeout` would, and it is off deliberately -- a short value
logged 44 subscribers out in a day, because a sleeping phone fails a keepalive
exactly like a departed one.

Why this happens here, constantly, rather than rarely: the pool is
192.168.88.10-254, which is 245 addresses, and on 2026-09-13 skylink held 222
bound leases against it -- 90% full, 23 to spare. A 15-minute lease is what
keeps that pool from running dry, so it cannot be lengthened; and at 90%
occupancy an address freed by a device that went quiet is handed straight to
somebody else. 59 sessions were standing on addresses that belonged to another
handset. A power cut does the whole estate at once, which is how it gets
noticed, but the churn is continuous.

The pool cannot simply be grown, either: widening the bridge subnet to /23 or
/22 runs into pppoe-pool on 192.168.89.x and free-internet-pool on
192.168.90.x. Growing it means moving the hotspot to another subnet, which is
a migration, not a setting.

So: RouterOS runs `lease-script` on every bind and unbind, which is the exact
moment an address changes hands. Removing the stale hotspot state there is
event-driven and immediate, costs no polling, and -- the part keepalive could
never manage -- touches only devices whose address has actually moved. A phone
asleep on a valid lease is never disturbed.

The periodic sweep in services/duplicate_sessions.py stays. This closes the
gap at source; that catches whatever a missed script run leaves behind, and a
router restored from an old backup or replaced outright comes back without
this until ensure_lease_script puts it there.
"""

import logging

logger = logging.getLogger(__name__)

# Marks the script as ours so it can be recognised and replaced without
# clobbering something an operator wrote by hand.
MARKER = "# smartbill:hotspot-lease-sync"

# RouterOS supplies $leaseBound ("1" bind, "0" unbind), $leaseActIP and
# $leaseActMAC. Kept to those three so it runs the same on 7.18 and 7.19.
#
# Unbind matches the departing MAC *and* the address it is giving up. Keying on
# the address alone would be the obvious reading -- the address is going back to
# the pool, so anything pinned to it is stale -- but it trusts RouterOS to fire
# the script before handing the address on. Matching the MAC as well needs no
# such assumption and reaps exactly as much: the session removed is the one that
# would otherwise be left for the next holder to collide with, which is the
# recycled-address case prevented rather than swept.
#
# Bind removes by MAC where the address DIFFERS: the device has moved and its
# old authorisation is what strands it. Two sessions on one MAC and two
# addresses is always stale -- shared-users lets one account serve several
# devices, never one device several addresses.
#
# A device removed here is not cut off: it re-runs MAC authentication on the
# host entry its next packet creates, which is the whole point of doing this
# at the moment the lease moves.
LEASE_SCRIPT = f"""{MARKER}
:local ip $leaseActIP
:local mac $leaseActMAC
:if ([:len $ip] = 0) do={{ :return }}
:if ($leaseBound = "0") do={{
  :if ([:len $mac] > 0) do={{
    :foreach s in=[/ip hotspot active find where address=$ip mac-address=$mac] do={{
      /ip hotspot active remove $s
    }}
    :foreach h in=[/ip hotspot host find where address=$ip mac-address=$mac] do={{
      /ip hotspot host remove $h
    }}
  }}
}} else={{
  :if ([:len $mac] > 0) do={{
    :foreach s in=[/ip hotspot active find where mac-address=$mac address!=$ip] do={{
      /ip hotspot active remove $s
    }}
    :foreach h in=[/ip hotspot host find where mac-address=$mac address!=$ip] do={{
      /ip hotspot host remove $h
    }}
  }}
}}"""


def hotspot_dhcp_servers(api):
    """
    The DHCP servers that hand out addresses to hotspot clients.

    Matched by interface against the hotspot servers rather than by name.
    "defconf" is what these two happen to be called; a third station set up by
    somebody else will not be, and a lease script on the wrong server would
    reap sessions for an interface that has no hotspot on it at all.
    """
    interfaces = set()
    for hs in api.path("ip", "hotspot"):
        if str(hs.get("disabled")) == "true":
            continue
        iface = str(hs.get("interface") or "")
        if iface:
            interfaces.add(iface)

    return [d for d in api.path("ip", "dhcp-server")
            if str(d.get("interface") or "") in interfaces]


def ensure_lease_script(router, api, *, apply=True):
    """
    Put the lease script on every hotspot DHCP server of one router.

    Returns (set, already_present, skipped) counts.

    Idempotent, and safe to run on a schedule: a router restored from backup,
    reset, or swapped for a spare comes back without the script, and the
    stranding starts again silently. Re-asserting it is what makes this
    permanent rather than a thing that was true once.

    A server carrying somebody else's script is left alone and reported. Ours
    is recognised by its marker comment; overwriting an operator's own script
    because it occupied the same field would be a poor trade for a fix that
    can wait for a human.
    """
    was_set = present = skipped = 0

    for server in hotspot_dhcp_servers(api):
        name = str(server.get("name"))
        current = str(server.get("lease-script") or "")

        if current.strip() == LEASE_SCRIPT.strip():
            present += 1
            continue

        if current.strip() and MARKER not in current:
            skipped += 1
            logger.warning(
                "[lease-script] %s on %s already has a script that is not "
                "ours — left alone", name, router.name)
            continue

        if not apply:
            was_set += 1
            continue

        try:
            api.path("ip", "dhcp-server").update(
                **{".id": server[".id"], "lease-script": LEASE_SCRIPT})
            was_set += 1
            logger.info(
                "[lease-script] installed on %s/%s — hotspot sessions now "
                "follow the lease", router.name, name)
        except Exception as exc:
            skipped += 1
            logger.warning(
                "[lease-script] could not install on %s/%s: %s",
                router.name, name, exc)

    return was_set, present, skipped


def ensure_lease_script_everywhere(*, apply=True, routers=None):
    """
    Assert the lease script across an operator's estate.

    Unreachable routers are skipped, not failed: this is a thing that should
    be true, re-checked on a schedule, and a router that is down will be
    caught by the next run.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    totals = [0, 0, 0]
    for router in (routers if routers is not None
                   else RouterDevice.objects.all_tenants().filter(
                       is_active=True)):
        api = safe_connect_router(router)
        if not api:
            logger.info("[lease-script] %s unreachable — skipped", router)
            continue
        for i, n in enumerate(ensure_lease_script(router, api, apply=apply)):
            totals[i] += n

    return tuple(totals)
