import logging
from celery import shared_task

from billing.notifications import (
    send_sms,
    send_whatsapp,
    notify_customer,
)

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
# UNIFIED NOTIFICATION TASK
# =====================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=20,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def notify_customer_task(self, phone: str, message: str) -> bool:
    """
    Sends SMS + WhatsApp in background.
    One channel failing does not stop the other.
    """
    notify_customer(phone, message)
    logger.info(f"[notify_customer_task] Notification sent to {phone}")
    return True
