import requests
from django.conf import settings
import africastalking

# ==================================
# SMS USING AFRICA'S TALKING
# ==================================

def send_sms(phone, message):
    try:
        africastalking.initialize(
            username=settings.AT_USERNAME,
            api_key=settings.AT_API_KEY,
        )
        sms = africastalking.SMS
        sms.send(message, [phone])
        return True
    except Exception as e:
        print("SMS ERROR:", e)
        return False


# ==================================
# WHATSAPP (META CLOUD API)
# ==================================

def send_whatsapp(phone, message):
    try:
        url = f"https://graph.facebook.com/v18.0/{settings.WHATSAPP_PHONE_ID}/messages"

        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": message},
        }

        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }

        requests.post(url, json=payload, headers=headers)
        return True
    except Exception as e:
        print("WHATSAPP ERROR:", e)
        return False


# ==================================
# UNIFIED SEND FUNCTION
# ==================================

def notify_customer(phone, message):
    send_sms(phone, message)
    send_whatsapp(phone, message)
