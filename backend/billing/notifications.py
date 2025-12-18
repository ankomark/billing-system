import requests
from billing.config import get_setting


# =====================================================
# 1️⃣ SEND SMS (Africa's Talking)
# =====================================================

def send_sms(phone: str, message: str) -> bool:
    """
    Sends SMS using Africa's Talking API.
    Credentials are loaded from SystemSetting (DB).
    """

    username = get_setting("AT_USERNAME")
    api_key = get_setting("AT_API_KEY")

    if not username or not api_key:
        print("⚠ SMS not sent — Missing Africa's Talking credentials")
        return False

    url = "https://api.africastalking.com/version1/messaging"

    payload = {
        "username": username,
        "to": phone,
        "message": message,
    }

    headers = {
        "apiKey": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=10)
        response.raise_for_status()
        print("✅ SMS sent:", response.json())
        return True

    except Exception as e:
        print("❌ SMS ERROR:", str(e))
        return False


# =====================================================
# 2️⃣ SEND WHATSAPP (Meta Cloud API)
# =====================================================

def send_whatsapp(phone: str, message: str) -> bool:
    """
    Sends WhatsApp message using Meta Cloud API.
    Phone must be in international format (e.g. 2547XXXXXXXX)
    """

    token = get_setting("WHATSAPP_TOKEN")
    phone_id = get_setting("WHATSAPP_PHONE_ID")

    if not token or not phone_id:
        print("⚠ WhatsApp not sent — Missing WhatsApp credentials")
        return False

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {
            "body": message
        },
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        print("✅ WhatsApp sent:", response.json())
        return True

    except Exception as e:
        print("❌ WHATSAPP ERROR:", str(e))
        return False


# =====================================================
# 3️⃣ UNIFIED CUSTOMER NOTIFICATION
# =====================================================

def notify_customer(phone: str, message: str) -> None:
    """
    Notify customer via:
    - SMS
    - WhatsApp

    Failure in one channel does NOT stop the other.
    """

    print(f"📢 Notifying customer {phone}")

    sms_sent = send_sms(phone, message)
    wa_sent = send_whatsapp(phone, message)

    if not sms_sent and not wa_sent:
        print("⚠ Notification failed on both SMS & WhatsApp")
