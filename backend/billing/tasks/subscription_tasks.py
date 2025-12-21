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
    Periodic task:
    - Expire subscriptions
    - Disable customer access safely
    Idempotent & safe to re-run.
    """

    now = timezone.now()

    expired_subs = (
        Subscription.objects
        .select_related("customer")
        .filter(status="active", expiry_date__lt=now)
    )

    processed = 0

    for sub in expired_subs:
        customer = sub.customer

        # 🔒 Atomic state change
        with transaction.atomic():
            # Re-check inside transaction
            sub.refresh_from_db()
            if sub.status != "active":
                continue

            sub.status = "expired"
            sub.save(update_fields=["status"])

            if customer.status != "expired":
                customer.status = "expired"
                customer.save(update_fields=["status"])

        # 🔌 Disable router access asynchronously
        disable_customer_task.delay(customer.id)

        processed += 1
        logger.info(
            f"[expiry] Subscription {sub.id} expired, customer {customer.id} disabled"
        )

    return processed
