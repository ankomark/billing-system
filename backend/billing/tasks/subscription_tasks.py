from celery import shared_task
from django.utils import timezone
import logging

from billing.models import Subscription
from billing.tasks.router_tasks import disable_customer_task

logger = logging.getLogger(__name__)


@shared_task
def enforce_subscription_expiry():
    """
    Disable access for expired subscriptions.
    Safe to run repeatedly.
    """

    now = timezone.now()

    expired_subs = Subscription.objects.select_related("customer").filter(
        status="active",
        expiry_date__lte=now,
    )

    count = 0

    for sub in expired_subs:
        sub.status = "expired"
        sub.save(update_fields=["status"])

        customer = sub.customer
        customer.status = "expired"
        customer.save(update_fields=["status"])

        disable_customer_task.delay(customer.id)

        count += 1

    logger.info(f"[expiry] Processed {count} expired subscriptions")
    return count
