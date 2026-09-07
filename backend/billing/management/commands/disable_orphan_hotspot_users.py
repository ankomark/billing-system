"""
Hotspot accounts still serving traffic for a device the database has released.

Provisioning writes a hotspot user onto the router named after the device MAC.
Every path that takes one off again walks outward from the customer's device
rows -- `disable_customer_access` iterates `hotspot_macs_for(customer)` -- so an
account whose row is gone is unreachable by all of them. It is not disabled, not
expired, and has no password, because provisioning authenticates a hotspot user
by MAC alone. The handset simply reassociates and RouterOS lets it in.

Found on 2026-09-07 while checking whether anyone was on the network without
paying. The database was clean: 1574 customers, 1574 paid invoices, nobody with
usage and no payment. The routers held 44 enabled accounts belonging to nobody,
7.30GB served between them:

    skylink    13 accounts, all enabled, 3.37GB
    skylink3   31 accounts, all enabled, 3.93GB

All 44 were evictions. Not deleted customers -- every one had an eviction record
in AccessAuditLog, from the ordinary case of a device limit reached and another
device connecting. Eviction deletes the CustomerDevice row unconditionally and
the router half was best-effort, so it left in two ways:

  * 20 asked the wrong router. The loop used `_routers_to_ask`, which is the
    assigned router alone, and subscribers move -- 126 failover rows, including
    a mass migration on 2026-08-31. It cleaned the router they were on now and
    left the account on the one they had been on.

  * 24 asked the right router and missed anyway, because an unreachable router
    was a bare `continue` with no log and no retry.

Both are fixed at the source: eviction now goes through `_kick_device`, which
asks every router the operator owns and hands what it cannot confirm to
kick_device_task for about an hour. This command is the reconciler behind that
retry -- for the outage that outlasts the hour, and for whatever else learns to
leave an account behind. The same shape as the cap sweep behind the polled
check, and as `sync_router_profiles` behind provisioning.

Nothing else would have caught them. `enforce_usage_caps` iterates Subscription,
so an account with no customer is invisible to it by construction, and 41 of the
44 carried no `limit-bytes-total` at all. `limit-uptime` was set but counts
session time, not wall-clock, so a three-week bundle sold in August had burned
minutes of it. Several of these MACs have ConnectionAttempt rows, one of them
182: the portal refused the device the whole time its router account let it on.

Disables. Does not delete.

An orphan is not proof of a freeloader. It is equally the fingerprint of a
paying subscriber whose record was lost, and deleting the account would throw
away the only remaining evidence of who they were -- the MAC, the profile they
were sold, the bytes they have run through it. Disabling refuses the next login
and keeps all of it, and is undone by setting one flag back. Removal is
available in `disable_hotspot` for callers that want it; this command
deliberately is not that.

    python manage.py disable_orphan_hotspot_users
    python manage.py disable_orphan_hotspot_users --fix
    python manage.py disable_orphan_hotspot_users --fix --router skylink3
"""

import re

from django.core.management.base import BaseCommand

from billing.models import Customer, CustomerDevice, RouterDevice
from billing.router_service import safe_connect_router
from billing.utils import normalize_mac

# What provisioning stamps on everything it creates. See enable_hotspot.
PROVISIONED_BY_US = "AUTO | WIFI BILLING SYSTEM"

# Twelve hex digits, however they were punctuated. normalize_mac deliberately
# does not reject a name it cannot parse -- it upper-cases it and hands it back
# so that a value we cannot read still matches itself -- so the shape has to be
# checked here rather than inferred from its output.
MAC_SHAPED = re.compile(r"^[0-9A-F]{12}$")

# How many orphans on one router is still a believable number.
#
# This command decides what to disable by subtracting a database query from a
# router's account list, and the failure mode of that shape is not subtle: if
# the query returns nothing -- a broken tenant context, a migration mid-flight,
# a filter that stops matching -- then every account on the router is missing
# from it, every account carries our provisioning comment, and the command
# disables the entire estate in one pass. It would be doing exactly what it was
# told, and every subscriber would be off.
#
# So there is a number above which the more likely explanation is that this
# command is wrong rather than the routers are. The first real run found 44
# across two routers, which was the whole backlog since the leak began; a
# reconciler running nightly against a fixed eviction path should be finding
# nought or a handful. Twenty-five is comfortably above that and far below the
# hundreds an empty query would produce.
DEFAULT_MAX_DISABLE = 25


def _is_mac(name):
    return bool(MAC_SHAPED.match(re.sub(r"[^0-9A-Fa-f]", "", name or "").upper()))


class Command(BaseCommand):
    help = (
        "Disable hotspot accounts on the routers that belong to no customer. "
        "Reports by default; --fix disables what it finds. Never deletes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Disable the accounts. Without it, nothing is written.")
        parser.add_argument(
            "--router",
            help="Limit to one router, by name. Default: every router.")
        parser.add_argument(
            "--max-disable", type=int, default=DEFAULT_MAX_DISABLE,
            help=(f"Refuse to act on a router with more than this many orphans "
                  f"(default {DEFAULT_MAX_DISABLE}). A number far above the "
                  f"steady state means the database half of the comparison is "
                  f"wrong, not that the estate is."))

    def handle(self, *args, **options):
        apply = options["fix"]
        max_disable = options["max_disable"]

        routers = RouterDevice.objects.all_tenants().order_by("id")
        if options.get("router"):
            routers = routers.filter(name=options["router"])
            if not routers.exists():
                self.stderr.write(self.style.ERROR(
                    f"No router named {options['router']!r}"))
                return

        total_orphan = total_disabled = total_bytes = 0
        skipped_foreign = []
        unreachable = []
        refused = []

        for router in routers:
            # Every MAC this operator knows, from both places one can live.
            # hotspot_username is the first device; the rest are CustomerDevice
            # rows, and an account matching either of them has an owner.
            known = {
                normalize_mac(mac) for mac in
                Customer.objects.all_tenants()
                .filter(tenant_id=router.tenant_id)
                .exclude(hotspot_username="")
                .values_list("hotspot_username", flat=True)
            }
            known |= {
                normalize_mac(mac) for mac in
                CustomerDevice.objects.all_tenants()
                .filter(tenant_id=router.tenant_id)
                .values_list("mac_address", flat=True)
            }

            # An operator with hotspot subscribers and not one known address is
            # not an operator whose accounts are all orphaned -- it is this
            # command failing to see the database. Refusing here is the
            # difference between a quiet no-op and disabling every subscriber
            # on the router.
            if not known and _has_hotspot_customers(router.tenant_id):
                refused.append((router.name, "the database returned no known "
                                             "addresses for this operator"))
                self.stdout.write(self.style.ERROR(
                    f"\n{router.name}: REFUSED -- this operator has hotspot "
                    f"subscribers but no known device addresses came back. "
                    f"Nothing was touched."))
                continue

            api = safe_connect_router(router)
            if not api:
                unreachable.append(router.name)
                self.stdout.write(
                    self.style.WARNING(f"\n{router.name}: unreachable"))
                continue

            users = api.path("ip", "hotspot", "user")
            try:
                rows = list(users)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f"\n{router.name}: could not read hotspot users: {exc}"))
                continue

            orphans, foreign = [], []
            for row in rows:
                name = str(row.get("name") or "")

                # A name that is not a MAC was not written by provisioning, and
                # the router's own `admin` is one of them. Nothing here should
                # ever be able to reach an operator's hand-made account.
                if not _is_mac(name):
                    continue
                if normalize_mac(name) in known:
                    continue

                # MAC-shaped, unowned, and not ours. Reported so it is not
                # invisible, skipped so this command never disables something
                # it cannot prove it created.
                if str(row.get("comment") or "").strip() != PROVISIONED_BY_US:
                    foreign.append(row)
                    continue

                orphans.append(row)

            already = [r for r in orphans
                       if str(r.get("disabled")).lower() in ("true", "yes")]
            live = [r for r in orphans if r not in already]

            served = sum(int(r.get("bytes-in") or 0) + int(r.get("bytes-out") or 0)
                         for r in orphans)
            total_orphan += len(orphans)
            total_bytes += served

            self.stdout.write(
                f"\n{router.name} ({router.ip_address}): {len(orphans)} orphan "
                f"account(s), {len(live)} still enabled, {_human(served)} served"
            )

            # Above the ceiling this stops being a reconciler and starts being
            # an outage. Reported in full so the number can be judged, and then
            # left alone -- raising the limit is a deliberate act with the list
            # already in front of you.
            if len(live) > max_disable:
                refused.append((router.name,
                                f"{len(live)} orphans is above --max-disable="
                                f"{max_disable}"))
                for row in sorted(live, key=_served, reverse=True):
                    self.stdout.write(
                        f"   {str(row.get('name')):<19} "
                        f"{str(row.get('profile') or '-'):<20} "
                        f"{_human(_served(row)):>9}")
                self.stdout.write(self.style.ERROR(
                    f"   REFUSED -- {len(live)} orphan(s) is more than "
                    f"--max-disable={max_disable}. Nothing was touched on "
                    f"{router.name}. Read the list above; if it is genuinely "
                    f"this large, re-run with --max-disable={len(live)}."))
                continue

            for row in sorted(live, key=_served, reverse=True):
                name = str(row.get("name"))
                line = (f"   {name:<19} {str(row.get('profile') or '-'):<20} "
                        f"{_human(_served(row)):>9}  "
                        f"cap={row.get('limit-bytes-total') or 'NONE'}")

                if not apply:
                    self.stdout.write(line)
                    continue

                try:
                    # Disable first, then end the session. The other order
                    # leaves a window where the session is gone and the
                    # account still works, and a device that reconnects inside
                    # a second is the normal case, not the unlucky one --
                    # disable_customer_access says the same thing about the
                    # PPPoE pair for the same reason.
                    users.update(**{".id": row[".id"], "disabled": "yes"})
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"{line}  FAILED: {exc}"))
                    continue

                total_disabled += 1
                ended = _end_session(api, name)
                note = "" if ended else "  (disabled; live session survived)"
                self.stdout.write(self.style.SUCCESS(f"{line}  disabled{note}"))

            for row in already:
                self.stdout.write(
                    f"   {str(row.get('name')):<19} {'':<20} "
                    f"{_human(_served(row)):>9}  already disabled")

            if foreign:
                skipped_foreign.extend((router.name, r) for r in foreign)

        self.stdout.write("")
        if skipped_foreign:
            self.stdout.write(self.style.WARNING(
                f"{len(skipped_foreign)} MAC-named account(s) belong to no "
                f"customer and were not created by this system. Left alone -- "
                f"decide on them by hand:"))
            for router_name, row in skipped_foreign:
                self.stdout.write(
                    f"   {router_name:<10} {str(row.get('name')):<19} "
                    f"comment={str(row.get('comment') or '')[:32]!r}")

        if refused:
            self.stdout.write(self.style.ERROR(
                f"{len(refused)} router(s) REFUSED and left untouched:"))
            for name, why in refused:
                self.stdout.write(f"   {name:<10} {why}")

        if unreachable:
            self.stdout.write(self.style.WARNING(
                f"{len(unreachable)} router(s) unreachable and unchecked: "
                f"{', '.join(unreachable)}. A router that comes back after an "
                f"outage serves whatever it had when it left, so re-run this "
                f"once it is up."))

        if not total_orphan:
            self.stdout.write(self.style.SUCCESS(
                "No orphan hotspot accounts on any reachable router."))
        elif apply:
            self.stdout.write(self.style.SUCCESS(
                f"Disabled {total_disabled} of {total_orphan} orphan "
                f"account(s). Nothing was deleted -- re-enable by setting "
                f"disabled=no on the router."))
        else:
            self.stdout.write(
                f"{total_orphan} orphan account(s), {_human(total_bytes)} "
                f"served. Nothing was changed. Pass --fix to disable them.")


def _has_hotspot_customers(tenant_id):
    """
    Whether an empty set of known addresses is believable for this operator.

    A tenant that genuinely sells no hotspot has no known addresses and no
    orphans either, and must not be refused for it.
    """
    return Customer.objects.all_tenants().filter(
        tenant_id=tenant_id, connection_type="hotspot").exists()


def _served(row):
    return int(row.get("bytes-in") or 0) + int(row.get("bytes-out") or 0)


def _human(n):
    n = int(n or 0)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f}GB"
    return f"{n / (1024 * 1024):.1f}MB"


def _end_session(api, mac_address):
    """
    Kick the live session, if there is one.

    Disabling the account is future tense -- RouterOS keeps an established
    session running until it times out on its own, which is the whole reason
    disable_hotspot ends the session as well as removing the user. Reported
    rather than raised: the account is already disabled by the time this runs,
    so a failure here means one device stays online until its session lapses,
    not that it keeps its access.
    """
    wanted = normalize_mac(mac_address)
    try:
        actives = api.path("ip", "hotspot", "active")
        for session in list(actives):
            if wanted in (normalize_mac(session.get("user")),
                          normalize_mac(session.get("mac-address"))):
                # Positional. librouteros' Path.remove takes ids as *args, so
                # a `.id` keyword raises TypeError -- a Python error, which
                # nothing guarding against an unreachable router would catch.
                actives.remove(session[".id"])
        return True
    except Exception:
        return False
