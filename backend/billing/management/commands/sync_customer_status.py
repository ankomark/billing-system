"""
Bring Customer.status into line with what each customer holds.

Reports by default; --fix writes. See billing.services.customer_status for why
the flag drifts and why both directions matter.
"""

from django.core.management.base import BaseCommand

from billing.services.customer_status import sync_customer_status


class Command(BaseCommand):
    help = ("Correct customers whose status disagrees with whether they hold "
            "a live paid subscription. Reports unless --fix is given.")

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true",
                            help="Actually write the corrections.")

    def handle(self, *args, **options):
        apply = options["fix"]
        activate, expire = sync_customer_status(apply=apply)

        verb = "Corrected" if apply else "Would correct"

        self.stdout.write(
            f"{verb} {len(activate)} customer(s) to active "
            f"(they hold a live paid subscription).")
        for c in activate[:30]:
            self.stdout.write(
                f"   cust {c.pk:5} {c.full_name[:20]:20} ({c.phone}) "
                f"was {c.status!r}")
        if len(activate) > 30:
            self.stdout.write(f"   ... and {len(activate) - 30} more")

        if activate:
            self.stdout.write(self.style.WARNING(
                "   These were skipped by enforce_usage_caps, which scans "
                "customer__status='active' — so their data cap was not being "
                "enforced."))

        self.stdout.write(
            f"\n{verb} {len(expire)} customer(s) to expired "
            f"(they hold nothing live).")
        for c in expire[:30]:
            self.stdout.write(
                f"   cust {c.pk:5} {c.full_name[:20]:20} ({c.phone}) "
                f"was {c.status!r}")
        if len(expire) > 30:
            self.stdout.write(f"   ... and {len(expire) - 30} more")

        if not apply:
            self.stdout.write(self.style.WARNING(
                "\nNothing was written. Re-run with --fix to apply."))
