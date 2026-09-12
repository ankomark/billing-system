"""
Customers holding several live subscriptions at once.

Every individual subscription here is correct. That is the point: this finds
nothing that any other check could find, because there is nothing wrong with
any one row. What it finds is an accumulation.

Customer 33 held nine live subscriptions on 2026-09-12, overlapping
continuously from 26 August to 9 October -- KSh 2,271 of packages, every one a
comp issued by the operator's own admin account, every one logged in
AdminActionLog with a reason ("Refund"), every one individually defensible.
Nothing in the system noticed the stack, and it only came to light because a
one-week package turned out to expire in 21 days.

The stack also multiplies devices, which is the part that costs bandwidth.
Packages sell two devices each; ten device rows spread across nine
subscriptions never breach a per-package limit, because each pair sits under
its own subscription. Five were connected at once on packages sold as
two-device.

Overlap on its own is normal and must not be flagged. Topping up -- buying two
hours with twenty minutes still on the clock -- creates exactly this shape, is
deliberately supported, and is why enforce_subscription_expiry checks whether
anything else still covers a customer before disconnecting them. So the default
threshold is three, not two: two live subscriptions is a top-up, and several is
a pattern worth a human looking at.

Reports. Never changes anything -- what to do about a stack is a judgement
about a customer, not a rule a sweep can apply.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from billing.models import CustomerDevice, Payment, Subscription

logger = logging.getLogger(__name__)

# Two live subscriptions is a top-up. Three is a pattern.
DEFAULT_MIN_LIVE = 3


@dataclass
class Stack:
    customer: object
    subscriptions: list = field(default_factory=list)
    comps: list = field(default_factory=list)
    devices: int = 0

    @property
    def live_count(self):
        return len(self.subscriptions)

    @property
    def comp_value(self):
        """List price of the comped packages, not what was invoiced.

        A comp settles at 0.00, so the invoice total says nothing about what
        was given away. The package price is what it would have cost.
        """
        return sum((s.package.price for s in self.comps), Decimal("0.00"))

    @property
    def device_allowance(self):
        """
        Devices the stack grants in total.

        Not the largest package's limit -- the sum. Each subscription carries
        its own allowance and its own device rows, so stacking adds them
        together. This is the number the customer can actually use, and it is
        never the number any single package advertises.
        """
        return sum(max(1, getattr(s.package, "max_devices", 1) or 1)
                   for s in self.subscriptions)

    @property
    def covered_until(self):
        return max(s.expiry_date for s in self.subscriptions)

    @property
    def covered_from(self):
        return min(s.start_date for s in self.subscriptions)


def find_stacked(*, min_live=DEFAULT_MIN_LIVE, now=None):
    """
    Customers with `min_live` or more live paid subscriptions, worst first.

    "Live" is the same question everything else asks -- active, paid, not yet
    expired -- so a stack of abandoned checkouts is not what this reports. The
    abandoned-checkout sweep handles those, and a customer whose rows it has
    closed disappears from here on the next run.
    """
    now = now or timezone.now()

    live = (Subscription.objects.all_tenants()
            .filter(status="active", invoice__payment_status="paid",
                    expiry_date__gt=now)
            .select_related("customer", "package", "invoice"))

    crowded = {row["customer"] for row in
               live.values("customer").annotate(n=Count("id"))
               if row["n"] >= min_live}
    if not crowded:
        return []

    comp_subs = set(
        Payment.objects.all_tenants()
        .filter(method="comp", subscription__customer__in=crowded)
        .values_list("subscription_id", flat=True))

    device_counts = dict(
        CustomerDevice.objects.all_tenants()
        .filter(customer__in=crowded)
        .values_list("customer")
        .annotate(n=Count("id")))

    stacks = {}
    for sub in live:
        if sub.customer_id not in crowded:
            continue
        stack = stacks.get(sub.customer_id)
        if stack is None:
            stack = stacks[sub.customer_id] = Stack(
                customer=sub.customer,
                devices=device_counts.get(sub.customer_id, 0))
        stack.subscriptions.append(sub)
        if sub.pk in comp_subs:
            stack.comps.append(sub)

    return sorted(stacks.values(),
                  key=lambda s: (-s.live_count, -s.comp_value))
