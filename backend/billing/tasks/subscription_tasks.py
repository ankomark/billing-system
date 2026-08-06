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
            still_covered = (
                Subscription.objects.all_tenants()
                .filter(
                    customer=customer,
                    status="active",
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
            continue

        disable_customer_task.delay(customer.id)
        logger.info(
            f"[expiry] Subscription {sub.id} expired — customer {customer.id} queued for disable"
        )

    logger.info(f"[expiry] Processed {processed} expired subscriptions")
    return processed
