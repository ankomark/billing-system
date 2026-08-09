"""
Put the hotspot-sharing detection rules on routers, or take them off again.

The sweep installs them itself for any operator whose policy is not off, so the
normal path needs none of this. It exists for the two moments the sweep cannot
cover: switching the feature off — where nothing would ever run again to clean
up after it — and looking at what is actually on a box when the answer matters.
"""

from django.core.management.base import BaseCommand, CommandError

from billing.models import RouterDevice, Tenant
from billing.router_service import safe_connect_router
from billing.services import tethering
from billing.tenancy import all_tenants, tenant_context


class Command(BaseCommand):
    help = "Install, remove or inspect the tethering-detection rules on routers"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("install", "remove", "status"))
        parser.add_argument(
            "--tenant",
            help="Operator slug or id. Omit to act on every operator's routers.",
        )
        parser.add_argument(
            "--router", type=int,
            help="One router by id, rather than all of an operator's.",
        )

    def handle(self, *args, **options):
        routers = self._routers(options)
        if not routers:
            raise CommandError("no active routers matched")

        for router in routers:
            with tenant_context(router.tenant_id):
                self._one(router, options["action"])

    def _routers(self, options):
        with all_tenants():
            qs = RouterDevice.objects.all_tenants().filter(is_active=True)

            if options.get("tenant"):
                value = options["tenant"]
                tenant = Tenant.objects.filter(slug=value).first()
                if tenant is None and str(value).isdigit():
                    tenant = Tenant.objects.filter(pk=int(value)).first()
                if tenant is None:
                    raise CommandError(f"no operator {value!r}")
                qs = qs.filter(tenant=tenant)

            if options.get("router"):
                qs = qs.filter(pk=options["router"])

            return list(qs.select_related("tenant"))

    def _one(self, router, action):
        api = safe_connect_router(router)
        if api is None:
            self.stderr.write(
                self.style.WARNING(f"{router.name}: unreachable — {router.last_error}"))
            return

        if action == "install":
            policy = tethering.policy(router.tenant_id)
            result = tethering.ensure_rules(
                api,
                timeout=tethering.list_timeout(router.tenant_id),
                connections=tethering.connection_limit(router.tenant_id),
                # Installing by hand must put the same thing on the router the
                # sweep would, or an operator debugging with this command is
                # looking at a box configured differently from every other one.
                block_lists=(
                    tethering.block_lists_for(router.tenant_id)
                    if policy == tethering.BLOCK else ()
                ))
            if policy == tethering.BLOCK:
                tethering.set_block_marker(router.tenant_id, True)
            self.stdout.write(self.style.SUCCESS(
                f"{router.name}: +{result['added']} ~{result['updated']} "
                f"-{result['removed']}"))
            return

        if action == "remove":
            removed = tethering.remove_rules(api)
            self.stdout.write(self.style.SUCCESS(
                f"{router.name}: removed {removed} rule(s) and queue(s)"))
            return

        lists = tethering.read_lists(api) or {}
        counts = ", ".join(f"{name}={len(addresses)}"
                           for name, addresses in lists.items())
        installed = sum(
            1 for row in api.path("ip", "firewall", "mangle")
            if (row.get("comment") or "").startswith(tethering.RULE_COMMENT)
        )
        expected = len(tethering.mangle_rules())
        policy = tethering.policy(router.tenant_id)
        style = self.style.SUCCESS if installed == expected else self.style.WARNING
        self.stdout.write(style(
            f"{router.name}: policy={policy} "
            f"rules={installed}/{expected} {counts}"))

        # The rules that stop traffic, counted separately and named, because
        # "ten rules installed" says nothing about whether anybody is cut off.
        blocking = [
            (row.get("src-address-list") or "?")
            for row in api.path("ip", "firewall", "filter")
            if (row.get("comment") or "").startswith(tethering.RULE_COMMENT)
        ]
        if blocking:
            cut_off = sum(len(lists.get(name, {})) for name in set(blocking))
            note = (f"{router.name}: rejecting traffic from "
                    f"{', '.join(sorted(set(blocking)))} — "
                    f"{cut_off} address(es) cut off right now")
            # Blocking under a policy that is not `block` is the state this is
            # here to surface: nothing sweeps to lift it, and the subscriber
            # behind it has no case, no text and no explanation.
            self.stdout.write(
                self.style.SUCCESS(note) if policy == tethering.BLOCK
                else self.style.ERROR(
                    f"{note} — but policy is {policy}, so nothing will lift "
                    f"this. Run: tethering_rules install (or remove)"))

        # The hole none of the rules can see into, and the reason an operator
        # might be looking at an empty table on a network that is being shared.
        if tethering.ipv6_is_open(api) is True:
            self.stdout.write(self.style.WARNING(
                f"{router.name}: forwarding IPv6 — the hotspot does not "
                f"intercept it and these rules do not see it. Sharing over "
                f"IPv6 goes undetected, and unauthenticated."))
