"""
Customers holding several live subscriptions at once.

Reports only. See billing.services.stacked_subscriptions for why overlap on
its own is normal and what makes a stack worth looking at.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.services.stacked_subscriptions import (
    DEFAULT_MIN_LIVE, find_stacked,
)


class Command(BaseCommand):
    help = ("List customers holding several live paid subscriptions at once. "
            "Reports only; changes nothing.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--min", type=int, default=DEFAULT_MIN_LIVE,
            help="How many live subscriptions before a customer is listed. "
                 "Two is an ordinary top-up. Default %(default)s.")
        parser.add_argument(
            "--comps-only", action="store_true",
            help="Only customers whose stack includes a comped package.")

    def handle(self, *args, **options):
        stacks = find_stacked(min_live=options["min"])
        if options["comps_only"]:
            stacks = [s for s in stacks if s.comps]

        if not stacks:
            self.stdout.write(self.style.SUCCESS(
                f"No customer holds {options['min']} or more live "
                f"subscriptions."))
            return

        self.stdout.write(
            f"{len(stacks)} customer(s) holding {options['min']}+ live "
            f"subscriptions:\n")

        for s in stacks:
            c = s.customer
            self.stdout.write(
                f"cust {c.pk} {c.full_name} ({c.phone}) — "
                f"{s.live_count} live, {len(s.comps)} comped, "
                f"{s.devices} device(s) against an allowance of "
                f"{s.device_allowance}")
            self.stdout.write(
                f"   covered {timezone.localtime(s.covered_from):%Y-%m-%d} "
                f"-> {timezone.localtime(s.covered_until):%Y-%m-%d} "
                f"continuously")
            if s.comps:
                self.stdout.write(self.style.WARNING(
                    f"   KSh {s.comp_value} of packages at no charge"))
            for sub in sorted(s.subscriptions, key=lambda x: x.expiry_date):
                tag = "COMP" if sub in s.comps else "paid"
                self.stdout.write(
                    f"      sub {sub.pk:6} {tag:4} "
                    f"{sub.package.name[:24]:24} "
                    f"{timezone.localtime(sub.start_date):%m-%d %H:%M} -> "
                    f"{timezone.localtime(sub.expiry_date):%m-%d %H:%M}")
            self.stdout.write("")

        # Said once, at the end, where it will actually be read.
        self.stdout.write(self.style.WARNING(
            "Nothing was changed. A stack is not a fault — every row in it is "
            "valid, and whether it should exist is a judgement about the "
            "customer, not a rule."))
