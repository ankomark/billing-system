"""
Bring hotspot limit-uptime back onto the wall clock.

Reports by default; --fix writes. See billing.services.uptime_alignment for
why the limit drifts and what it is a backstop for.
"""

from django.core.management.base import BaseCommand

from billing.services.uptime_alignment import align_uptime_limits


class Command(BaseCommand):
    help = ("Correct hotspot accounts whose limit-uptime has drifted away "
            "from the wall clock. Reports unless --fix is given.")

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true",
                            help="Actually write the corrected limits.")

    def handle(self, *args, **options):
        apply = options["fix"]
        checked, corrected, overdue = align_uptime_limits(apply=apply)

        verb = "Corrected" if apply else "Would correct"
        self.stdout.write(f"Checked {checked} account(s) with live paid cover.")
        self.stdout.write(f"{verb} {corrected}.")

        if overdue:
            self.stdout.write(self.style.WARNING(
                f"\n{len(overdue)} account(s) have no live paid subscription. "
                f"Left to the disable sweep, which removes them outright:"))
            for rname, mac, cid in overdue[:20]:
                self.stdout.write(f"   {rname:9} {mac} cust {cid}")
            if len(overdue) > 20:
                self.stdout.write(f"   ... and {len(overdue) - 20} more")

        if not apply:
            self.stdout.write(self.style.WARNING(
                "\nNothing was written. Re-run with --fix to apply."))
