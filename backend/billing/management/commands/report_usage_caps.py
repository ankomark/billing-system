"""
Who would be cut off if data-cap enforcement were switched on right now.

Read-only, deliberately and completely: it opens no router connection, sends no
message, and writes nothing to the database. Run it against production before
enforcement is enabled, and again after, and it will tell you the same thing
either way.

It exists because of the shape of this particular change. Caps have never been
enforced on this platform -- the task that does it was written and never
scheduled -- so the moment it is switched on, the first sweep does not find the
handful of subscribers who went over in the last five minutes. It finds every
subscriber who has gone over at any point since their current subscription
started, all at once, and disconnects all of them inside one beat interval.

That may be nobody. It may be a third of the estate. Nobody knows, and "deploy
it and watch" is a bad way to find out when the thing being watched is paying
customers losing their connection and getting an SMS about it.

    python manage.py report_usage_caps
    python manage.py report_usage_caps --tenant skylink
    python manage.py report_usage_caps --over-only
"""

from django.core.management.base import BaseCommand

from billing.models import Subscription, Tenant
from billing.services.usage import MB, cap_bytes_for, usage_since, window_start


def _human(n):
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f}GB"
    return f"{n / MB:.0f}MB"


class Command(BaseCommand):
    help = (
        "Report which subscribers are over their data cap. Changes nothing -- "
        "no router is contacted, no message is sent, no row is written."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            help="Limit to one operator, by slug. Default: every operator.",
        )
        parser.add_argument(
            "--over-only",
            action="store_true",
            help="List only those already over their cap, not those near it.",
        )
        parser.add_argument(
            "--warn-at",
            type=int,
            default=80,
            help="Percentage at which a subscriber is worth listing (default 80).",
        )

    def handle(self, *args, **options):
        # The same query the sweep runs, and paid-only for the same reason:
        # every subscription is born active with an unpaid invoice, so an
        # abandoned purchase is not what anybody is being served on.
        subscriptions = (
            Subscription.objects.all_tenants()
            .select_related("customer", "package", "tenant")
            .filter(status="active", customer__status="active",
                    invoice__payment_status="paid")
            .order_by("customer_id", "-expiry_date")
        )

        slug = options.get("tenant")
        if slug:
            try:
                tenant = Tenant.objects.get(slug=slug)
            except Tenant.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"No operator with slug {slug!r}"))
                return
            subscriptions = subscriptions.filter(tenant_id=tenant.id)

        warn_at = options["warn_at"]
        over_only = options["over_only"]

        seen = set()
        rows = []
        capped_total = 0

        for sub in subscriptions.iterator(chunk_size=200):
            # One subscription per customer -- the longest-lived paid one,
            # which is what enable_customer_access provisions against.
            if sub.customer_id in seen:
                continue
            seen.add(sub.customer_id)

            cap = cap_bytes_for(sub.customer, sub)
            if not cap:
                continue  # unlimited
            capped_total += 1

            used = usage_since(sub.customer, window_start(sub))
            percent = used / cap * 100

            if used >= cap or (not over_only and percent >= warn_at):
                rows.append((percent, sub, used, cap))

        rows.sort(key=lambda r: r[0], reverse=True)

        over = [r for r in rows if r[2] >= r[3]]

        self.stdout.write("")
        self.stdout.write(
            f"{capped_total} active paid subscriber(s) have a data cap.")
        self.stdout.write(
            self.style.WARNING(
                f"{len(over)} of them are ALREADY OVER it and would be "
                f"disconnected by the first sweep."
            ) if over else self.style.SUCCESS(
                "None of them are over it. Enabling enforcement disconnects "
                "nobody today."
            )
        )
        self.stdout.write("")

        if not rows:
            return

        header = f"{'':>6}  {'OPERATOR':<14} {'SUBSCRIBER':<22} {'USED':>9} / {'CAP':<9}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for percent, sub, used, cap in rows:
            line = (
                f"{percent:>5.0f}%  "
                f"{(sub.tenant.slug or '')[:14]:<14} "
                f"{sub.customer.full_name[:22]:<22} "
                f"{_human(used):>9} / {_human(cap):<9}"
            )
            self.stdout.write(
                self.style.ERROR(line) if used >= cap else line)

        self.stdout.write("")
        self.stdout.write(
            "Nothing was changed. To enforce, unset USAGE_CAPS_DRY_RUN "
            "(or set it to 0) and let the beat schedule run."
        )
