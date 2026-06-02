import logging
from celery import shared_task

from billing.notifications import send_sms, send_whatsapp

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_kwargs={"max_retries": 5},
    retry_jitter=True,
)
def send_sms_task(self, phone: str, message: str) -> bool:
    result = send_sms(phone, message)
    if not result:
        raise Exception(f"SMS delivery failed to {phone}")
    logger.info(f"[sms] Sent to {phone}")
    return True


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=15,
    retry_kwargs={"max_retries": 5},
    retry_jitter=True,
)
def send_whatsapp_task(self, phone: str, message: str) -> bool:
    result = send_whatsapp(phone, message)
    if not result:
        raise Exception(f"WhatsApp delivery failed to {phone}")
    logger.info(f"[whatsapp] Sent to {phone}")
    return True


@shared_task
def notify_customer_task(phone: str, message: str) -> None:
    """Fan-out: each channel handles its own retries."""
    send_sms_task.delay(phone, message)
    send_whatsapp_task.delay(phone, message)
    logger.info(f"[notify] Queued SMS+WhatsApp for {phone}")


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def dispatch_broadcast_task(
    self,
    audience: str,
    channel: str,
    message: str,
    customer_ids: list,
) -> dict:
    """
    Fan-out broadcast to a filtered customer set.
    Uses .iterator() so the entire customer table is never loaded into memory.
    Called from AdminBroadcastView as a single async task, avoiding blocking
    the web worker with an N-iteration loop.
    """
    from billing.models import Customer

    qs = Customer.objects.only("id", "phone")

    if audience == "active":
        qs = qs.filter(status="active")
    elif audience == "expired":
        qs = qs.filter(status="expired")
    elif audience == "custom":
        qs = qs.filter(id__in=customer_ids)
    # "all" → no additional filter

    sent = failed = 0
    for customer in qs.iterator(chunk_size=200):
        try:
            if channel == "sms":
                send_sms_task.delay(customer.phone, message)
            else:
                send_whatsapp_task.delay(customer.phone, message)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.error(f"[broadcast] Failed to queue for {customer.phone}: {exc}")

    logger.info(f"[broadcast] Queued {sent} messages ({failed} failed) — {channel}/{audience}")
    return {"sent": sent, "failed": failed, "channel": channel, "audience": audience}
