import logging
from celery import shared_task

from billing.notifications import send_sms, send_whatsapp

logger = logging.getLogger(__name__)


# =====================================================
# SMS TASK
# =====================================================

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
        raise Exception("SMS sending failed")

    logger.info(f"[send_sms_task] SMS sent to {phone}")
    return True


# =====================================================
# WHATSAPP TASK
# =====================================================

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
        raise Exception("WhatsApp sending failed")

    logger.info(f"[send_whatsapp_task] WhatsApp sent to {phone}")
    return True


# =====================================================
# FAN-OUT NOTIFICATION TASK (NO RETRIES)
# =====================================================

@shared_task
def notify_customer_task(phone: str, message: str) -> None:
    """
    Fan-out notification task.
    Each channel handles its own retries.
    """
    send_sms_task.delay(phone, message)
    send_whatsapp_task.delay(phone, message)

    logger.info(f"[notify_customer_task] Notification queued for {phone}")
