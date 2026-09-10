"""
Compare, or upload, the captive-portal files on an operator's routers.

Until this existed the files went on by hand in WinBox, once per router, per
change -- so they drift and nothing notices. On 2026-09-10 both live routers
were still serving a login.html from before a fix made two days earlier.

Reports by default. Nothing is written unless --push is given.

    python manage.py portal_files                        # what differs
    python manage.py portal_files --router 5 --push      # upload
    python manage.py portal_files --push --backup /tmp/portal-backup

config.js is rebuilt per router from the template with that router's tokens,
and is uploaded only if the copy already on the router contains no line the
rebuild lacks -- so a file somebody edited by hand is refused, not clobbered.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from billing.models import RouterDevice, Tenant
from billing.router_service import safe_connect_router
from billing.services import portal_files
from billing.tenancy import all_tenants, tenant_context

DEFAULT_FILES = ["config.js", "login.html", "alogin.html", "logout.html",
                 "status.html", "md5.js"]


class Command(BaseCommand):
    help = "Show or upload the captive-portal files on routers"

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Operator slug or id")
        parser.add_argument("--router", type=int, help="One router by id")
        parser.add_argument("--push", action="store_true",
                            help="Upload what differs, instead of only reporting")
        parser.add_argument("--backup", help="Directory to save the replaced files")
        parser.add_argument("--files", help="Comma-separated names, default: "
                                            + ",".join(DEFAULT_FILES))
        parser.add_argument("--api-base",
                            help="Overrides the API address written into config.js")

    def handle(self, *args, **options):
        routers = self._routers(options)
        if not routers:
            raise CommandError("no active routers matched")

        api_base = options.get("api_base") or self._api_base()
        names = [n.strip() for n in
                 (options.get("files") or ",".join(DEFAULT_FILES)).split(",")
                 if n.strip()]

        stale = 0
        for router in routers:
            with tenant_context(router.tenant_id):
                stale += self._one(router, api_base, names, options)

        if stale and not options["push"]:
            self.stdout.write(self.style.WARNING(
                f"\n{stale} file(s) differ. Re-run with --push to upload."))

    def _api_base(self):
        """
        What goes into config.js as API_BASE.

        PLATFORM_BASE_URL is the API's own address — it is what each operator's
        M-Pesa callback URL is built from — and the portal wants that with
        /api on the end. Refused rather than guessed if it is unset: a portal
        pointed at the wrong host fails in the browser while the server logs
        nothing at all.
        """
        site = (getattr(settings, "PLATFORM_BASE_URL", "") or "").strip()
        if not site:
            raise CommandError(
                "PLATFORM_BASE_URL is unset, so there is nothing to write into "
                "config.js as API_BASE — set it, or pass --api-base")
        return site.rstrip("/") + "/api"

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

    def _one(self, router, api_base, names, options):
        api = safe_connect_router(router)
        if api is None:
            self.stdout.write(f"{router.name}: unreachable")
            return 0

        results = portal_files.push(
            router, api, api_base,
            tunnel_ip=getattr(settings, "WG_SERVER_TUNNEL_IP", "10.10.0.1"),
            iface=getattr(settings, "WG_INTERFACE_NAME", "wg-smartbill"),
            names=names,
            backup_dir=options.get("backup"),
            apply=options["push"],
        )

        self.stdout.write(f"{router.name}:")
        differ = 0
        for name, before, after, action in results:
            if action == "same":
                self.stdout.write(f"   {name:14} unchanged ({after} bytes)")
            elif action == "missing":
                self.stdout.write(self.style.WARNING(
                    f"   {name:14} not in the portal folder"))
            elif action == "refused":
                self.stdout.write(self.style.ERROR(
                    f"   {name:14} REFUSED — the router's copy has lines the "
                    f"rebuild lacks; someone edited it by hand"))
            elif action == "updated":
                self.stdout.write(self.style.SUCCESS(
                    f"   {name:14} uploaded {before} -> {after} bytes"))
            else:
                differ += 1
                self.stdout.write(f"   {name:14} differs {before} -> {after} bytes")

        left = portal_files.leftover_rules(api)
        if left:
            self.stdout.write(self.style.ERROR(
                f"   !! {len(left)} temporary firewall rule(s) left behind — "
                f"remove them"))
        return differ
