"""
Close subscription rows left behind by checkouts nobody completed.

Reports by default and changes nothing; pass --fix to write. See
billing.services.abandoned_checkouts for what it refuses to touch and why.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand

from billing.services.abandoned_checkouts import (
    DEFAULT_GRACE, close_abandoned_checkouts,
)


class Command(BaseCommand):
    help = ("Expire active-but-unpaid subscriptions left behind by abandoned "
            "M-Pesa prompts. Reports unless --fix is given.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Actually expire them. Without this, nothing is written.")
        parser.add_argument(
            "--grace-hours", type=float,
            default=DEFAULT_GRACE.total_seconds() / 3600,
            help="How recent a checkout has to be to count as still in "
                 "flight rather than abandoned. Default %(default)s.")

    def handle(self, *args, **options):
        grace = timedelta(hours=options["grace_hours"])
        apply = options["fix"]

        result = close_abandoned_checkouts(grace=grace, apply=apply)

        verb = "Closed" if apply else "Would close"
        self.stdout.write(
            f"{verb} {len(result.closed_rows)} abandoned checkout(s).")
        self.stdout.write(
            f"Left {result.in_flight} alone as still in flight "
            f"(started within {options['grace_hours']}h).")

        for sub in result.closed_rows[:40]:
            self.stdout.write(
                f"   sub {sub.pk:6} cust {sub.customer_id:5} "
                f"{sub.customer.full_name[:20]:20} "
                f"{sub.package.name[:22]:22} {sub.invoice.total_amount:>8} "
                f"started {sub.start_date:%Y-%m-%d %H:%M} "
                f"would have run to {sub.expiry_date:%Y-%m-%d}")
        if len(result.closed_rows) > 40:
            self.stdout.write(f"   ... and {len(result.closed_rows) - 40} more")

        if result.unsettled_grants:
            # Not a fault of this sweep, and never closed by it. Surfaced
            # because nothing else looks for them: the customer is online on a
            # comp somebody granted, and the missing Payment row means the
            # invoice still reads unpaid and the giveaway is not counted.
            self.stdout.write(self.style.WARNING(
                f"\n{len(result.unsettled_grants)} free grant(s) are still "
                f"marked unpaid — the comp payment was never recorded. Left "
                f"alone; record the payment to settle the invoice:"))
            for sub in result.unsettled_grants:
                self.stdout.write(
                    f"   sub {sub.pk:6} cust {sub.customer_id:5} "
                    f"{sub.customer.full_name[:20]:20} "
                    f"{sub.package.name[:22]:22} "
                    f"invoice {sub.invoice.invoice_number} "
                    f"granted {sub.start_date:%Y-%m-%d} "
                    f"runs to {sub.expiry_date:%Y-%m-%d}")

        if result.needs_a_human:
            # Loud, and never closed automatically. An unpaid row with money
            # against it is a reconciliation gap: the customer was debited and
            # the invoice never caught up. Expiring it would take away service
            # that was paid for.
            self.stderr.write(self.style.ERROR(
                f"\n{len(result.banked)} row(s) are unpaid in the database but "
                f"have money against them. Left alone — reconcile by hand:"))
            for sub, money in result.banked:
                self.stderr.write(self.style.ERROR(
                    f"   sub {sub.pk} cust {sub.customer_id} "
                    f"invoice {sub.invoice.invoice_number} "
                    f"({sub.invoice.payment_status}) -> {money!r}"))

        if not apply:
            self.stdout.write(self.style.WARNING(
                "\nNothing was written. Re-run with --fix to apply."))
