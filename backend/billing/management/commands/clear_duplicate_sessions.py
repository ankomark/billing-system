"""
Close the sessions a handset left behind when it picked up a new DHCP lease.

Reports by default; --fix closes them. See billing.services.duplicate_sessions
for why they accumulate and how the live one is told from the dead one.
"""

from django.core.management.base import BaseCommand

from billing.router_service import ros_duration_seconds
from billing.services.duplicate_sessions import (
    MIN_IDLE_SECONDS, clear_duplicate_sessions, find_duplicate_sessions,
)


def _secs(row, field):
    return ros_duration_seconds(row.get(field)) or 0


class Command(BaseCommand):
    help = ("Close duplicate hotspot sessions -- the same MAC holding more "
            "than one, which happens when a device gets a new DHCP lease "
            "before the old session ends. Reports unless --fix is given.")

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true",
                            help="Actually close the stale sessions.")
        parser.add_argument(
            "--min-idle", type=int, default=MIN_IDLE_SECONDS,
            help=(f"Only close a session idle at least this many seconds "
                  f"(default {MIN_IDLE_SECONDS}). The stale ones are idle for "
                  f"hours; this guards the case of two genuinely busy "
                  f"sessions, which is left for a human."))

    def handle(self, *args, **options):
        apply = options["fix"]
        min_idle = options["min_idle"]

        if not apply:
            found = find_duplicate_sessions(min_idle=min_idle)
            total_drop = 0
            for router, _api, mac, keep, rest, drop in found:
                total_drop += len(drop)
                self.stdout.write(
                    f"{router.name:9} {mac}  {len(rest) + 1} sessions")
                self.stdout.write(
                    f"   KEEP {str(keep.get('address')):16} "
                    f"up {str(keep.get('uptime')):12} "
                    f"idle {str(keep.get('idle-time'))}")
                for r in rest:
                    verb = "DROP" if r in drop else "keep (still busy)"
                    self.stdout.write(
                        f"   {verb:4} {str(r.get('address')):16} "
                        f"up {str(r.get('uptime')):12} "
                        f"idle {str(r.get('idle-time'))}")
            self.stdout.write(
                f"\n{len(found)} MAC(s) with duplicates, {total_drop} stale "
                f"session(s) would be closed.")
            self.stdout.write(self.style.WARNING(
                "Nothing was changed. Re-run with --fix to close them."))
            return

        closed, busy = clear_duplicate_sessions(apply=True, min_idle=min_idle)
        self.stdout.write(self.style.SUCCESS(
            f"Closed {closed} stale session(s)."))
        if busy:
            self.stdout.write(self.style.WARNING(
                f"{len(busy)} extra session(s) were still in use and left "
                f"alone -- two busy sessions on one address is not what this "
                f"cleans up:"))
            for rname, mac, r in busy[:20]:
                self.stdout.write(
                    f"   {rname:9} {mac} idle {r.get('idle-time')}")
