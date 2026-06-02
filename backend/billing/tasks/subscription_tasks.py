import logging
from celery import shared_task
from django.utils import timezone
from django.db import transaction

from billing.models import Subscription
from billing.tasks.router_tasks import disable_customer_task

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
        Subscription.objects
        .select_related("customer")
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

            if customer.status != "expired":
                customer.status = "expired"
                customer.save(update_fields=["status"])

        disable_customer_task.delay(customer.id)
        processed += 1
        logger.info(
            f"[expiry] Subscription {sub.id} expired — customer {customer.id} queued for disable"
        )

    logger.info(f"[expiry] Processed {processed} expired subscriptions")
    return processed
