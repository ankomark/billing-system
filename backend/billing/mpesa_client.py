import base64
import requests
from datetime import datetime
from billing.config import get_setting


# =====================================================
# ACCESS TOKEN
# =====================================================

def get_mpesa_access_token():
    """
    Fetch OAuth token using Consumer Key + Secret stored in DB (SystemSetting)
    """
    env = get_setting("MPESA_ENV", "sandbox")
    consumer_key = get_setting("MPESA_CONSUMER_KEY")
    consumer_secret = get_setting("MPESA_CONSUMER_SECRET")

    if not consumer_key or not consumer_secret:
        raise ValueError("Missing M-Pesa Consumer Key or Consumer Secret")

    url = (
        "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        if env == "production"
        else "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    )

    response = requests.get(url, auth=(consumer_key, consumer_secret), timeout=15)
    response.raise_for_status()

    return response.json()["access_token"]


# =====================================================
# PASSWORD GENERATION
# =====================================================

def generate_password():
    """
    Generate Base64 encoded password for STK Push
    """
    shortcode = get_setting("MPESA_SHORTCODE", "174379")
    passkey = get_setting("MPESA_PASSKEY")

    if not passkey:
        raise ValueError("Missing MPESA_PASSKEY in system settings")

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    data = f"{shortcode}{passkey}{timestamp}"

    encoded = base64.b64encode(data.encode()).decode()
    return encoded, timestamp


# =====================================================
# STK PUSH
# =====================================================

def initiate_stk_push(
    phone_number,
    amount,
    account_reference,
    description="WiFi Payment",
):
    """
    Initiates an STK Push using DB-stored credentials
    """

    access_token = get_mpesa_access_token()
    password, timestamp = generate_password()

    env = get_setting("MPESA_ENV", "sandbox")
    shortcode = get_setting("MPESA_SHORTCODE", "174379")
    callback_url = get_setting("MPESA_CALLBACK_URL")

    if not callback_url:
        raise ValueError("Missing MPESA_CALLBACK_URL in system settings")

    url = (
        "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        if env == "production"
        else "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone_number,
        "PartyB": shortcode,
        "PhoneNumber": phone_number,
        "CallBackURL": callback_url,
        "AccountReference": account_reference,
        "TransactionDesc": description,
    }

    response = requests.post(url, json=payload, headers=headers, timeout=20)
    response.raise_for_status()

    return response.json()
