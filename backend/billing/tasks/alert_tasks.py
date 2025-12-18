from celery import shared_task
import logging
from billing.services.notification_service import notify_customer  # or your admin notify method

logger = logging.getLogger(__name__)

@shared_task
def notify_admin_task(message: str):
    """
    You can change this to email/Slack/WhatsApp.
    For now we can SMS your admin phone (store in SystemSetting).
    """
    try:
        # Example: send to admin phone stored in SystemSetting key ADMIN_PHONE
        from billing.config import get_setting
        admin_phone = get_setting("ADMIN_PHONE")
        if admin_phone:
            notify_customer(admin_phone, message)
        logger.warning(f"[ADMIN ALERT] {message}")
    except Exception:
        logger.exception("Failed to notify admin")
