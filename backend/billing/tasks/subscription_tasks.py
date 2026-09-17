import logging
from celery import shared_task
from django.utils import timezone
from django.db import transaction

from billing.models import Subscription
from billing.tasks.router_tasks import disable_customer_task
from billing.tenancy import all_tenants

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def enforce_subscription_expiry(self):
    """
    Expire active subscriptions whose expiry_date has passed.
    Idempotent — safe to re-run. Uses .iterator() to avoid loading
    all expired subscriptions into memory at once.
    """
    now = timezone.now()
    processed = 0

    qs = (
        Subscription.objects.all_tenants()
        .select_related("customer", "tenant")
        .filter(status="active", expiry_date__lt=now)
    )

    for sub in qs.iterator(chunk_size=100):
        customer = sub.customer

        with transaction.atomic():
            sub.refresh_from_db()
            if sub.status != "active":
                continue

            sub.status = "expired"
            sub.save(update_fields=["status"])

            # Is anything else still keeping this customer online?
            #
            # Expiry was per-subscription and the disable was per-customer, so
            # one running out cut off everything that customer had. A top-up —
            # buying two hours with twenty minutes still on the clock — was
            # therefore self-defeating: the old package expired, the customer
            # was disabled, and the time they had just paid for went with it.
            # Their dashboard showed an active subscription and no internet.
            #
            # Evaluated after the row above is marked expired, so it cannot
            # count itself.
            # Paid, not merely active -- the same rule every grant path
            # carries, and the one place it was missing that hands out service
            # rather than withholding it.
            #
            # Every subscription is born `active` with an unpaid invoice, so an
            # abandoned M-Pesa prompt leaves a row that answers yes here. When
            # the customer's real package then ran out, this concluded they
            # were still covered and skipped the disable entirely: no
            # disconnect, no customer.status change, and the longer the package
            # they had walked away from paying for, the longer they stayed on.
            # A 250/- three-week prompt abandoned in August was still
            # suppressing expiry in September.
            #
            # Found on 2026-09-12 auditing the 316 sessions then live. 90 such
            # rows existed across 63 subscribers; for 35 of them the unpaid row
            # outlasted every paid one, and 29 of those had paid nothing at all.
            still_covered = (
                Subscription.objects.all_tenants()
                .filter(
                    customer=customer,
                    status="active",
                    invoice__payment_status="paid",
                    expiry_date__gt=timezone.now(),
                )
                .exists()
            )

            if not still_covered and customer.status != "expired":
                customer.status = "expired"
                customer.save(update_fields=["status"])

        processed += 1

        if still_covered:
            logger.info(
                f"[expiry] Subscription {sub.id} expired — customer "
                f"{customer.id} left connected, another subscription is live"
            )
            _drop_devices_left_uncovered(sub)
            continue

        disable_customer_task.delay(customer.id)
        logger.info(
            f"[expiry] Subscription {sub.id} expired — customer {customer.id} queued for disable"
        )

    logger.info(f"[expiry] Processed {processed} expired subscriptions")
    return processed


def _drop_devices_left_uncovered(sub):
    """
    Take off the devices this package was serving that nothing else covers.

    "Another subscription is live" is a question about the customer, and
    leaving every device connected on that answer assumed their packages all
    served the same phones. They need not: somebody with three weeks on one
    phone who buys three hours for a second holds two packages for two phones.
    When the three hours end, the second phone's time is over whatever the
    first phone holds -- and left alone it rode out the rest of its
    limit-uptime, which counts connected time, not the clock.

    A device is kept when a live package still covers it: its own, or one paid
    for and not yet redeemed on any device, which is a renewal waiting for its
    code. A top-up redeemed on the same phone has already moved the binding
    off this package, so there is nothing here to drop.
    """
    from billing.models import CustomerDevice
    from billing.services.entitlement import subscription_for_device
    from billing.tasks.router_tasks import kick_device_task

    customer = sub.customer
    macs = (
        CustomerDevice.objects.all_tenants()
        .filter(tenant_id=sub.tenant_id, subscription=sub)
        .values_list("mac_address", flat=True)
    )
    for mac in macs:
        if subscription_for_device(customer, mac) is not None:
            continue
        kick_device_task.delay(customer.id, mac, only_if_uncovered=True)
        logger.info(
            f"[expiry] Subscription {sub.id} expired — {mac} of customer "
            f"{customer.id} has no package of its own left, queued for removal"
        )


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def close_abandoned_checkouts_task(self):
    """
    Nightly sweep of the subscription rows abandoned checkouts leave behind.

    Every tap on a package writes an `active` subscription before any money
    moves, so an ignored M-Pesa prompt leaves a row that reads as an
    entitlement. They accumulate daily and nothing has ever removed them: 90
    were live across 63 subscribers when this was written, the oldest from
    August.

    Scheduled for the same reason the orphan sweep is. The call sites that
    misread these rows are fixed, but "no query gets this wrong" is a property
    of every query written from now on, and this makes it stop mattering.

    Runs in the quiet hour and well away from enforce_subscription_expiry's
    five-minute cycle, so the two are never deciding a customer's status at the
    same moment.
    """
    from billing.services.abandoned_checkouts import close_abandoned_checkouts

    result = close_abandoned_checkouts(apply=True)

    if result.needs_a_human:
        # error, not warning: somebody was debited and the invoice never
        # caught up, and nothing else in the system is looking for that.
        logger.error(
            "[abandoned] %s subscription(s) are unpaid but have money against "
            "them and were left alone; reconcile by hand: %s",
            len(result.banked), [s.pk for s, _ in result.banked])

    logger.info("[abandoned] closed %s, left %s in flight",
                result.closed, result.in_flight)
    return result.closed


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
    retry_jitter=True,
)
def flag_stacked_subscriptions_task(self):
    """
    Notice when a customer accumulates live subscriptions.

    Nothing else can: every row in a stack is active, paid and inside its own
    expiry, so each one answers yes to every check the system makes. Customer
    33 held nine at once on 2026-09-12, overlapping continuously for six weeks,
    and it surfaced only because a one-week package turned out to expire in 21
    days.

    Logged rather than acted on. Overlap is how a top-up works, and what to do
    about a stack is a judgement about a customer rather than a rule a sweep
    can apply.
    """
    from billing.services.stacked_subscriptions import find_stacked

    stacks = find_stacked()
    if not stacks:
        logger.info("[stacked] no customer holds 3 or more live subscriptions")
        return 0

    for s in stacks:
        logger.warning(
            "[stacked] customer %s (%s) holds %s live subscriptions, %s of "
            "them comped (KSh %s at no charge), %s devices against an "
            "allowance of %s, covered to %s",
            s.customer.pk, s.customer.phone, s.live_count, len(s.comps),
            s.comp_value, s.devices, s.device_allowance,
            s.covered_until.date())
    return len(stacks)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
    retry_jitter=True,
)
def sync_customer_status_task(self):
    """
    Keep Customer.status agreeing with what each customer holds.

    Several paths write this flag and several clear it, and they do not cover
    each other -- the abandoned-checkout sweep, the window trim, an operator
    expiring a row by hand, and a subscription already expired when the sweep
    ran all end a subscription without touching it.

    Both directions cost something. A stale `expired` on a paying customer is
    skipped by enforce_usage_caps, which scans customer__status="active", so
    their data cap stops being enforced. A stale `active` inflates every
    dashboard counting the flag and puts them in the set auto-failover migrates.

    Cheap: two indexed queries and at most two bulk updates, usually of
    nothing.
    """
    from billing.services.customer_status import sync_customer_status

    activate, expire = sync_customer_status(apply=True)
    if activate or expire:
        logger.info("[status] %s activated, %s expired",
                    len(activate), len(expire))
    return len(activate) + len(expire)
