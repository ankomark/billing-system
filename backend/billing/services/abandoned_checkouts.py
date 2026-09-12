"""
Close the subscription rows left behind by checkouts nobody completed.

A subscription is created the moment somebody taps a package, before any money
moves -- Subscription.save writes the row and its unpaid invoice together. If
the M-Pesa prompt is then ignored, the row stays `active` inside its window
forever. Nothing has ever cleaned them up.

They are not harmless litter. `active` with a future expiry is what the whole
codebase means by "this customer is entitled to service", and an abandoned
250/- three-week prompt outlasts the 10/- three hours somebody actually bought.
Each such row is a loaded gun for any query that asks what a customer is on and
sorts by expiry. Two had already gone off by the time this was written -- the
manual-migration branch of AdminMigrateCustomerView, and the `still_covered`
check in enforce_subscription_expiry, which skipped the disconnect when a real
package ran out because an abandoned one was still "active".

Both are fixed at the call site. This removes the ammunition, and keeps
removing it, because the rows accrue every day and the next query to get this
wrong has not been written yet.

What it will not touch:

  * anything whose money arrived. A successful MpesaTransaction or any Payment
    row against the subscription means this is a reconciliation gap, not an
    abandoned checkout, and expiring it would take service from somebody whose
    payment we banked. Those are reported, never closed -- they need a human.
  * anything still in flight. An STK push sent minutes ago whose callback has
    not landed is not abandoned. `grace` is what separates the two.
  * anything already paid, expired or suspended. Only live, unpaid rows.

Marks `expired` rather than deleting. The row is the record that somebody tried
to buy and did not finish, which is worth keeping -- it is the difference
between a customer who never came and one the payment flow lost. `expired` is
also the existing vocabulary for "grants nothing"; there is no `cancelled`
status and inventing one would need a migration for no gain.

Writes through the queryset rather than save(), deliberately. Subscription.save
creates an invoice when the pk is None and sends PPPoE credentials by SMS; none
of that should fire, and update() cannot make it.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from billing.models import Invoice, MpesaTransaction, Payment, Subscription

logger = logging.getLogger(__name__)

# Long enough that a slow callback is never mistaken for an abandoned prompt.
# An STK push expires on Safaricom's side in about a minute and the callback
# normally lands in seconds, but callbacks have arrived late before and the
# cost of waiting is one more row sitting in a table for an hour, against the
# cost of being wrong, which is cutting off somebody who paid.
DEFAULT_GRACE = timedelta(hours=2)


@dataclass
class Outcome:
    closed: int = 0
    in_flight: int = 0
    banked: list = field(default_factory=list)
    closed_rows: list = field(default_factory=list)
    # Comps whose Payment never landed: granted on purpose, unpaid on paper.
    unsettled_grants: list = field(default_factory=list)

    @property
    def needs_a_human(self):
        return bool(self.banked)


def _has_money_against_it(subscription):
    """
    Did anything actually get paid for this row?

    A receipt counts even when the transaction status does not say success.
    The receipt number is issued by Safaricom and only exists once the customer
    has been debited, so a row carrying one is money in the account whatever
    our own status column ended up saying.
    """
    banked = MpesaTransaction.objects.all_tenants().filter(
        invoice=subscription.invoice)
    for tx in banked:
        if tx.status == "success" or tx.mpesa_receipt:
            return tx
    payment = Payment.objects.all_tenants().filter(
        subscription=subscription).first()
    return payment


def find_abandoned(*, grace=DEFAULT_GRACE, now=None):
    """
    Live, unpaid subscriptions older than the grace window.

    Zero-amount invoices are excluded, and the reason is not a special case:
    nobody abandons an M-Pesa prompt for nothing. An STK push cannot be sent
    for zero, so a row billed at 0.00 did not come from a checkout at all --
    it was written by hand. In this database every one of them is a comp, free
    internet granted to a subscriber who had been let down, and they are
    recognisable by more than the price: the expiry is deliberately stretched
    past what the package would give (a 6-hour bundle set to run a month).

    Six of the eight settled properly through Payment(method="comp"), which
    marks the invoice paid. Two did not -- the subscription and its zeroed
    invoice were written, the comp payment was not -- so they sit `pending`
    and look exactly like an abandoned checkout to anything reading the
    invoice alone. Sweeping those away would take back a decision an operator
    made on purpose, which is the same class of harm as closing a row somebody
    paid for.
    """
    now = now or timezone.now()
    return (
        Subscription.objects.all_tenants()
        .filter(status="active",
                expiry_date__gt=now,
                start_date__lt=now - grace)
        .exclude(invoice__payment_status="paid")
        .exclude(invoice__total_amount=0)
        .select_related("customer", "package", "invoice")
        .order_by("start_date")
    )


def find_unsettled_grants(*, now=None):
    """
    Zero-amount rows still marked unpaid: comps whose payment never landed.

    Reported so they are visible, never closed. They are already granting
    service -- the subscription is active and the customer is on the network --
    so the harm is only to the record: revenue_by_method does not count them,
    and every query that reads the invoice sees an unpaid customer online.
    """
    now = now or timezone.now()
    return (
        Subscription.objects.all_tenants()
        .filter(status="active", expiry_date__gt=now, invoice__total_amount=0)
        .exclude(invoice__payment_status="paid")
        .select_related("customer", "package", "invoice")
        .order_by("start_date")
    )


def close_abandoned_checkouts(*, grace=DEFAULT_GRACE, apply=False, now=None):
    """
    Expire abandoned checkouts. Reports what it would do unless `apply`.

    Idempotent: a row it has already closed is no longer `active`, so a second
    run finds nothing. Safe to schedule.
    """
    now = now or timezone.now()
    result = Outcome()

    # Counted separately so the report can say how many were left alone for
    # being recent rather than implying the table was nearly empty.
    result.in_flight = (
        Subscription.objects.all_tenants()
        .filter(status="active", expiry_date__gt=now,
                start_date__gte=now - grace)
        .exclude(invoice__payment_status="paid")
        .exclude(invoice__total_amount=0)
        .count()
    )

    result.unsettled_grants = list(find_unsettled_grants(now=now))

    for sub in find_abandoned(grace=grace, now=now).iterator(chunk_size=100):
        money = _has_money_against_it(sub)
        if money is not None:
            result.banked.append((sub, money))
            logger.error(
                "[abandoned] subscription %s (customer %s, invoice %s) is "
                "unpaid in the database but has money against it (%r) — left "
                "alone, this needs reconciling by hand",
                sub.pk, sub.customer_id, sub.invoice.invoice_number, money)
            continue

        result.closed_rows.append(sub)
        if not apply:
            continue

        with transaction.atomic():
            # Re-read under the lock. A callback landing between the scan and
            # this write would have flipped the invoice to paid, and closing
            # it then is the one thing this must never do.
            #
            # The invoice is fetched separately rather than select_related.
            # Invoice points at Subscription, so pulling it in here is a
            # reverse join onto the nullable side, and Postgres refuses
            # `FOR UPDATE cannot be applied to the nullable side of an outer
            # join`. Only the subscription row needs locking anyway.
            fresh = (Subscription.objects.all_tenants()
                     .select_for_update()
                     .get(pk=sub.pk))
            settled = Invoice.objects.all_tenants().filter(
                subscription=fresh, payment_status="paid").exists()
            if fresh.status != "active" or settled:
                result.closed_rows.pop()
                continue

            Subscription.objects.all_tenants().filter(pk=fresh.pk).update(
                status="expired")
            result.closed += 1

    if apply:
        logger.info(
            "[abandoned] closed %s checkout(s) nobody completed; %s left "
            "in flight; %s need reconciling by hand",
            result.closed, result.in_flight, len(result.banked))
    return result
