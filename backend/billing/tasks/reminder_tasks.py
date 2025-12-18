from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging

from billing.models import Subscription, ExpiryReminderLog
from billing.tasks.notification_tasks import notify_customer_task

logger = logging.getLogger(__name__)


@shared_task
def send_expiry_reminders():
    today = timezone.now().date()

    rules = [
        ("3_days", today + timedelta(days=3)),
        ("1_day", today + timedelta(days=1)),
    ]

    sent = 0

    for reminder_type, target_date in rules:
        subs = Subscription.objects.select_related("customer").filter(
            status="active",
            expiry_date__date=target_date,
        )

        for sub in subs:
            if ExpiryReminderLog.objects.filter(
                subscription=sub,
                reminder_type=reminder_type,
            ).exists():
                continue

            message = (
                f"Reminder: Your internet package expires on "
                f"{sub.expiry_date:%d %b %Y %I:%M %p}.\n"
                "Renew early to avoid interruption.\n"
                "Skylink ISP"
            )

            notify_customer_task.delay(sub.customer.phone, message)

            ExpiryReminderLog.objects.create(
                subscription=sub,
                reminder_type=reminder_type,
            )

            sent += 1

    logger.info(f"[reminders] Sent {sent} reminders")
    return sent
