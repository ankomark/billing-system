import base64
import logging
import requests
from datetime import datetime

from django.conf import settings

from billing.config import get_setting

logger = logging.getLogger(__name__)

# Credentials every operator must supply before they can take payments. Each is
# theirs alone — this is what makes subscriber money settle into their own till
# rather than the platform's.
REQUIRED_MPESA_KEYS = (
    "MPESA_CONSUMER_KEY",
    "MPESA_CONSUMER_SECRET",
    "MPESA_SHORTCODE",
    "MPESA_PASSKEY",
)


class PaymentsNotConfigured(Exception):
    """
    The operator has not finished M-Pesa onboarding.

    Distinct from a transient Daraja failure: this is waiting on a human to
    obtain credentials from Safaricom, so callers should surface it to the
    operator rather than retry.
    """


# =====================================================
# CONFIGURATION
# =====================================================

def missing_mpesa_keys(tenant=None):
    """Which required credentials this operator still lacks."""
    return [k for k in REQUIRED_MPESA_KEYS if not get_setting(k, tenant=tenant)]


def payments_configured(tenant=None) -> bool:
    return not missing_mpesa_keys(tenant=tenant)


def callback_url_for(tenant=None) -> str:
    """
    Where Safaricom should post this operator's results.

    Prefers an explicit MPESA_CALLBACK_URL if the operator set one — some
    deployments sit behind a bespoke gateway. Otherwise derives a per-operator
    URL carrying their public token, so the callback loads the right
    credentials without having to infer the operator from the payload.
    """
    explicit = get_setting("MPESA_CALLBACK_URL", tenant=tenant)
    if explicit:
        return explicit

    base = (getattr(settings, "PLATFORM_BASE_URL", "") or "").rstrip("/")
    token = getattr(tenant, "public_token", None)
    if not base or not token:
        raise PaymentsNotConfigured(
            "No M-Pesa callback URL. Set MPESA_CALLBACK_URL for this operator, "
            "or set PLATFORM_BASE_URL so a per-operator callback can be derived."
        )
    return f"{base}/api/mpesa/callback/{token}/"


def _require_configured(tenant):
    missing = missing_mpesa_keys(tenant=tenant)
    if missing:
        name = getattr(tenant, "name", "this operator")
        raise PaymentsNotConfigured(
            f"M-Pesa is not configured for {name}. Missing: {', '.join(missing)}."
        )


# =====================================================
# ACCESS TOKEN
# =====================================================

def get_mpesa_access_token(tenant=None):
    """
    Fetch OAuth token using this operator's Consumer Key + Secret.
    """
    env = get_setting("MPESA_ENV", "sandbox", tenant=tenant)
    consumer_key = get_setting("MPESA_CONSUMER_KEY", tenant=tenant)
    consumer_secret = get_setting("MPESA_CONSUMER_SECRET", tenant=tenant)

    if not consumer_key or not consumer_secret:
        raise PaymentsNotConfigured(
            "Missing M-Pesa Consumer Key or Consumer Secret for this operator"
        )

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

def generate_password(tenant=None):
    """
    Generate Base64 encoded password for STK Push, from this operator's
    shortcode and passkey.
    """
    shortcode = get_setting("MPESA_SHORTCODE", "174379", tenant=tenant)
    passkey = get_setting("MPESA_PASSKEY", tenant=tenant)

    if not passkey:
        raise PaymentsNotConfigured("Missing MPESA_PASSKEY for this operator")

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
    tenant=None,
):
    """
    Initiate an STK Push against one operator's shortcode.

    Every credential is read for `tenant` (or the tenant in context), so the
    charge appears on that operator's till and settles to their account.
    """
    _require_configured(tenant)

    access_token = get_mpesa_access_token(tenant=tenant)
    password, timestamp = generate_password(tenant=tenant)

    env = get_setting("MPESA_ENV", "sandbox", tenant=tenant)
    shortcode = get_setting("MPESA_SHORTCODE", "174379", tenant=tenant)
    callback_url = callback_url_for(tenant=tenant)

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
