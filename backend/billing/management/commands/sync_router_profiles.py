"""
What each router is actually serving, against what the database says it should.

These two drift, and the drift is invisible from both ends. The package page
shows 2M, the router hands out 8M, and nothing anywhere reports a
disagreement -- the subscriber is provisioned, the dashboard is green, and the
only symptom is bandwidth leaving the building.

Two ways it happens, and both were found live on 2026-09-05 while throttling
the estate to 2M for capacity:

  * a profile whose rate drifted from its package. `skylink` was serving
    44M/4M on a package the database had had at 2M for as long as anyone could
    remember. ensure_hotspot_profile used to return on sight of the profile
    name without looking at what was in it, so nothing ever corrected one.

  * an orphan from an older device count. Profiles are named
    HOTSPOT_PKG_<package>_D<devices>, so editing max_devices renames the
    profile and abandons the old one. Nobody new is put on it -- and every
    subscriber already provisioned under it stays there, at whatever rate it
    had. Three of those were serving 4M and 8M.

Reports by default and changes nothing. Pass --fix to correct what it finds.

    python manage.py sync_router_profiles
    python manage.py sync_router_profiles --fix
    python manage.py sync_router_profiles --fix --router skylink3
"""

import re

from django.core.management.base import BaseCommand

from billing.models import Package, RouterDevice
from billing.router_service import safe_connect_router

HOTSPOT_NAME = re.compile(r"^HOTSPOT_PKG_(\d+)_D(\d+)$")
PPPOE_NAME = re.compile(r"^PPPOE_PKG_(\d+)$")


class Command(BaseCommand):
    help = (
        "Compare each router's package profiles against the database. Reports "
        "by default; --fix corrects the rate limits it finds wrong."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Correct the rate limits. Without it, nothing is written.")
        parser.add_argument(
            "--router",
            help="Limit to one router, by name. Default: every router.")

    def handle(self, *args, **options):
        apply = options["fix"]
        packages = {p.id: p for p in Package.objects.all_tenants()}

        routers = RouterDevice.objects.all_tenants().order_by("id")
        if options.get("router"):
            routers = routers.filter(name=options["router"])
            if not routers.exists():
                self.stderr.write(self.style.ERROR(
                    f"No router named {options['router']!r}"))
                return

        total_wrong = total_fixed = total_orphan = 0
        unreachable = []

        for router in routers:
            api = safe_connect_router(router)
            if not api:
                unreachable.append(router.name)
                self.stdout.write(
                    self.style.WARNING(f"\n{router.name}: unreachable"))
                continue

            wrong = []
            for kind, path_args, pattern in (
                ("hotspot", ("ip", "hotspot", "user", "profile"), HOTSPOT_NAME),
                ("pppoe", ("ppp", "profile"), PPPOE_NAME),
            ):
                path = api.path(*path_args)
                try:
                    rows = list(path)
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(
                        f"{router.name}: could not read {kind} profiles: {exc}"))
                    continue

                for row in rows:
                    m = pattern.match(str(row.get("name", "")))
                    if not m:
                        continue
                    pkg = packages.get(int(m.group(1)))
                    if pkg is None:
                        continue

                    want = f"{pkg.upload_speed}M/{pkg.download_speed}M"
                    have = row.get("rate-limit")
                    if have == want:
                        continue

                    current = (
                        f"HOTSPOT_PKG_{pkg.id}_D{max(1, pkg.max_devices or 1)}"
                        if kind == "hotspot" else f"PPPOE_PKG_{pkg.id}"
                    )
                    orphan = row["name"] != current
                    total_orphan += orphan
                    total_wrong += 1

                    note = " (orphan)" if orphan else ""
                    line = f"   {row['name']:<26} {have!s:<10} -> {want:<10} {pkg.name}{note}"

                    if apply:
                        try:
                            # Rate only. shared-users on an orphan matches the
                            # device count in its own name, and the subscribers
                            # sitting on it were sold that allowance.
                            path.update(**{".id": row[".id"], "rate-limit": want})
                            total_fixed += 1
                        except Exception as exc:
                            line += f"  FAILED: {str(exc)[:40]}"
                    wrong.append(line)

            header = f"\n{router.name} ({router.ip_address})"
            if wrong:
                self.stdout.write(self.style.ERROR(
                    f"{header}: {len(wrong)} profile(s) disagree with the database"))
                for line in wrong:
                    self.stdout.write(line)
            else:
                self.stdout.write(self.style.SUCCESS(f"{header}: in step"))

        self.stdout.write("")
        if unreachable:
            self.stdout.write(self.style.WARNING(
                f"{len(unreachable)} router(s) unreachable and unchecked: "
                f"{', '.join(unreachable)}. A router that comes back after a "
                f"long outage serves whatever it had when it left, so re-run "
                f"this once it is up."
            ))

        if not total_wrong:
            self.stdout.write(self.style.SUCCESS(
                "Every reachable router matches the database."))
            return

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f"Corrected {total_fixed} of {total_wrong} profile(s) "
                f"({total_orphan} orphaned)."))
        else:
            self.stdout.write(self.style.ERROR(
                f"{total_wrong} profile(s) disagree ({total_orphan} orphaned). "
                f"Nothing was changed -- pass --fix to correct them."))
