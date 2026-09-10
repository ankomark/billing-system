"""
Check, or correct, the hotspot server settings on an operator's routers.

These three are answered by hand in the MikroTik setup wizard when a site is
built, and nothing has ever checked them since. All three were wrong on live
hardware on 2026-09-10 and each was taking customers off the air in a way that
looked like a different problem:

    address-pool=default-dhcp   the hotspot competed with DHCP for one 245
                                address range, filled it, and every new arrival
                                was refused an address -- "connected, no
                                internet", with no sign-in page
    addresses-per-mac=2         halved the site's capacity again
    login-by=cookie,http-chap   a paid-for television could never get on,
                                because both methods need the device to open
                                a browser

Status by default. Nothing is written unless --fix is given: a hotspot server
carries every subscriber's traffic, and a sweep that rewrites one on its own
is not a trade worth making.

    python manage.py hotspot_settings
    python manage.py hotspot_settings --router 5 --fix
"""

from django.core.management.base import BaseCommand, CommandError

from billing.models import RouterDevice, Tenant
from billing.router_service import safe_connect_router
from billing.services import hotspot_server
from billing.tenancy import all_tenants, tenant_context


class Command(BaseCommand):
    help = "Show or correct hotspot server settings on routers"

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Operator slug or id")
        parser.add_argument("--router", type=int, help="One router by id")
        parser.add_argument(
            "--fix", action="store_true",
            help="Correct what disagrees, instead of only reporting it")

    def handle(self, *args, **options):
        routers = self._routers(options)
        if not routers:
            raise CommandError("no active routers matched")

        wrong = 0
        for router in routers:
            with tenant_context(router.tenant_id):
                wrong += self._one(router, options["fix"])

        if wrong and not options["fix"]:
            self.stdout.write(self.style.WARNING(
                f"\n{wrong} router(s) disagree. Re-run with --fix to correct."))

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

    def _one(self, router, fix):
        api = safe_connect_router(router)
        if api is None:
            self.stdout.write(f"{router.name}: unreachable")
            return 0

        drift = hotspot_server.check(api)
        if not drift:
            self.stdout.write(self.style.SUCCESS(f"{router.name}: correct"))
            return 0

        self.stdout.write(f"{router.name}:")
        for key, (found, want) in sorted(drift.items()):
            self.stdout.write(f"   {key}: {found!r} -> should be {want!r}")

        if not fix:
            return 1

        changed = hotspot_server.apply(api, drift)
        for key, (found, want) in sorted(changed.items()):
            self.stdout.write(self.style.SUCCESS(
                f"   fixed {key}: {found!r} -> {want!r}"))
        return 0
