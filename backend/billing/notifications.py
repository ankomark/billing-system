import logging
import requests
from billing.config import get_setting

logger = logging.getLogger(__name__)

_DEFAULT_WA_VERSION = "v18.0"


def send_sms(phone: str, message: str) -> bool:
    """Send SMS via Africa's Talking. Credentials loaded from SystemSetting (DB)."""
    username = get_setting("AT_USERNAME")
    api_key  = get_setting("AT_API_KEY")

    if not username or not api_key:
        logger.warning("[sms] Skipped — missing Africa's Talking credentials in SystemSetting")
        return False

    url = "https://api.africastalking.com/version1/messaging"
    payload = {"username": username, "to": phone, "message": message}
    headers = {
        "apiKey": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(f"[sms] Sent to {phone}")
        return True
    except Exception as exc:
        logger.error(f"[sms] Failed to {phone}: {exc}")
        return False


def send_whatsapp(phone: str, message: str) -> bool:
    """
    Send WhatsApp message via Meta Cloud API.
    Phone must be in international format (e.g. 2547XXXXXXXX).
    API version is configurable via SystemSetting WHATSAPP_API_VERSION.
    """
    token    = get_setting("WHATSAPP_TOKEN")
    phone_id = get_setting("WHATSAPP_PHONE_ID")
    version  = get_setting("WHATSAPP_API_VERSION") or _DEFAULT_WA_VERSION

    if not token or not phone_id:
        logger.warning("[whatsapp] Skipped — missing WhatsApp credentials in SystemSetting")
        return False

    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(f"[whatsapp] Sent to {phone}")
        return True
    except Exception as exc:
        logger.error(f"[whatsapp] Failed to {phone}: {exc}")
        return False


def notify_customer(phone: str, message: str) -> None:
    """
    Notify via both SMS and WhatsApp. Failure in one channel does not stop
    the other. Designed to be called synchronously (e.g. from model.save()).
    For async delivery use notify_customer_task instead.
    """
    sms_ok = send_sms(phone, message)
    wa_ok  = send_whatsapp(phone, message)

    if not sms_ok and not wa_ok:
        logger.warning(f"[notify] Both SMS and WhatsApp failed for {phone}")
