import logging
import re

import requests

from billing.config import get_setting

logger = logging.getLogger(__name__)

_DEFAULT_WA_VERSION = "v18.0"

# ─── BlessedTexts ────────────────────────────────────────────────────────────

BLESSEDTEXTS_BASE = "https://sms.blessedtexts.com/api"
SMS_ENDPOINT = f"{BLESSEDTEXTS_BASE}/sms/v1/sendsms"
BALANCE_ENDPOINT = f"{BLESSEDTEXTS_BASE}/sms/v1/credit-balance"

SUCCESS = "1000"

# The provider answers HTTP 200 for a refusal and puts the reason in the body,
# so these are the difference between "sent" and "silently discarded".
STATUS_MEANINGS = {
    "1000": "Success",
    "1001": "Missing API key",
    "1002": "Invalid API key",
    "1003": "Missing sender ID",
    "1004": "Invalid sender ID",
    "1005": "Missing message",
    "1006": "Missing phone number",
    "1007": "Invalid phone number format",
    "1008": "Invalid phone number",
    "1009": "Out of SMS credit",
}

# The failures no amount of retrying fixes, and which affect every message
# rather than one. An empty account or a rejected key needs a person.
NEEDS_ATTENTION = {"1001", "1002", "1003", "1004", "1009"}


# ─── Numbers as the provider wants them ──────────────────────────────────────

COUNTRY_CODE = "254"

# The first digit of a Kenyan mobile subscriber number, after the country code
# or the trunk zero. 7xx is the long-standing range; 1xx was opened later and
# is now widely issued, so leaving it out would have quietly excluded every
# newer line.
MOBILE_PREFIXES = ("7", "1")

SUBSCRIBER_DIGITS = 9


def normalise_phone(phone):
    """
    A number in the form BlessedTexts accepts: 254712345678.

    Subscribers give their number the way they say it — 0712 345 678 — and
    that is what gets typed into the customer form, so that is what was going
    on the wire. The provider answers 1007/1008 and the message is silently
    discarded; the subscriber's expiry warning or voucher code never arrives,
    and nothing in the product says why.

    Done here, at the boundary to the provider, rather than on the way into the
    database. The provider's format is the provider's business, and normalising
    on save would fix nothing for the rows already stored — every existing
    customer would still need a migration to be reachable.

    Anything this cannot confidently read is returned exactly as given. That is
    the important half: a number we cannot parse should reach the provider
    untouched and be refused by it, because the alternative to a visible
    refusal is guessing, and a wrong guess sends a subscriber's voucher code to
    a stranger who did nothing but own the number we invented.
    """
    if not phone:
        return phone

    raw = str(phone).strip()
    digits = re.sub(r"\D", "", raw)

    # 00 is the international prefix dialled from a handset; + is the written
    # form of the same thing and is already gone with the non-digits.
    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith(COUNTRY_CODE):
        local = digits[len(COUNTRY_CODE):]
    elif digits.startswith("0"):
        local = digits[1:]
    else:
        local = digits

    if len(local) == SUBSCRIBER_DIGITS and local.startswith(MOBILE_PREFIXES):
        return COUNTRY_CODE + local

    return raw


def _credentials(tenant=None):
    return (
        get_setting("BLESSEDTEXTS_API_KEY", tenant=tenant),
        get_setting("BLESSEDTEXTS_SENDER_ID", tenant=tenant),
    )


def _describe(entry):
    """One result row as (accepted, human-readable reason)."""
    if not isinstance(entry, dict):
        return False, "Unreadable response from the SMS provider"
    code = str(entry.get("status_code", "")).strip()
    if code == SUCCESS:
        return True, "Success"
    reason = entry.get("status_desc") or STATUS_MEANINGS.get(code, "Unknown error")
    return False, f"{reason} ({code})" if code else reason


def _post(url, payload, timeout=15):
    response = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def send_sms(phone: str, message: str, tenant=None) -> bool:
    """
    Send one SMS through BlessedTexts, on the operator's own account.

    Returns True only when the provider accepted it.

    That distinction is the whole point of this rewrite. The previous version
    called raise_for_status() and returned True — but this provider answers HTTP
    200 for a refusal and puts the reason in the body, so a message rejected for
    an empty account or a bad number was reported as sent. An expiry warning
    nobody received, recorded as delivered, is worse than a visible failure.
    """
    api_key, sender_id = _credentials(tenant)
    if not api_key or not sender_id:
        logger.warning("[sms] Skipped — BlessedTexts is not set up for this operator")
        return False

    # Subscribers are stored the way they gave their number. The provider wants
    # one form and refuses the rest.
    phone = normalise_phone(phone)

    payload = {
        "api_key": api_key,
        "sender_id": sender_id,
        "message": message,
        "phone": phone,
    }

    try:
        body = _post(SMS_ENDPOINT, payload)
    except Exception as exc:
        logger.error("[sms] Request to %s failed: %s", phone, exc)
        return False

    # A send answers with a list; a malformed request can answer with a bare
    # object. Both shapes are handled rather than assumed.
    entries = body if isinstance(body, list) else [body]
    if not entries:
        logger.error("[sms] Empty response for %s", phone)
        return False

    ok, reason = _describe(entries[0])
    if ok:
        logger.info("[sms] Sent to %s", phone)
    else:
        logger.error("[sms] Refused for %s: %s", phone, reason)
        _flag_if_serious(entries[0], tenant)
    return ok


def send_bulk_sms(pairs, tenant=None):
    """
    Different messages to different numbers, in one request.

    `pairs` is an iterable of (phone, message).

    One request rather than one per recipient. A broadcast to four hundred
    subscribers was four hundred round trips, each able to fail on its own, and
    the provider supports doing it in a single call — which is also the
    difference between a broadcast finishing and a task timing out halfway
    through it.

    Returns {"sent": int, "failed": int, "errors": [str]}.
    """
    pairs = [(p, m) for p, m in pairs if p and m]
    if not pairs:
        return {"sent": 0, "failed": 0, "errors": []}

    api_key, sender_id = _credentials(tenant)
    if not api_key or not sender_id:
        logger.warning("[sms] Bulk skipped — BlessedTexts is not set up for this operator")
        return {
            "sent": 0,
            "failed": len(pairs),
            "errors": ["SMS is not set up for this operator"],
        }

    payload = {
        "api_key": api_key,
        "sender_id": sender_id,
        "messages": [
            {"phone": normalise_phone(phone), "message": message}
            for phone, message in pairs
        ],
    }

    try:
        body = _post(SMS_ENDPOINT, payload, timeout=60)
    except Exception as exc:
        logger.error("[sms] Bulk request failed: %s", exc)
        return {"sent": 0, "failed": len(pairs), "errors": [str(exc)]}

    entries = body if isinstance(body, list) else [body]
    sent = failed = 0
    errors = []
    for entry in entries:
        ok, reason = _describe(entry)
        if ok:
            sent += 1
        else:
            failed += 1
            phone = entry.get("phone", "unknown") if isinstance(entry, dict) else "unknown"
            errors.append(f"{phone}: {reason}")
            _flag_if_serious(entry, tenant)

    # One row comes back per recipient the provider handled. Anyone it did not
    # answer for was not sent, and counting them as failed beats implying they
    # went out.
    unanswered = len(pairs) - len(entries)
    if unanswered > 0:
        failed += unanswered
        errors.append(f"{unanswered} recipient(s) got no response from the provider")

    if failed:
        logger.warning("[sms] Bulk: %s sent, %s failed", sent, failed)
    return {"sent": sent, "failed": failed, "errors": errors[:20]}


def sms_balance(tenant=None):
    """
    Remaining SMS credit on this operator's account.

    Returns {"ok": bool, "balance": int|None, "error": str|None}.

    Worth having because running out is invisible from inside the product: every
    send fails, the customer hears nothing, and the only trace is a log line.
    """
    api_key, _ = _credentials(tenant)
    if not api_key:
        return {"ok": False, "balance": None, "error": "No BlessedTexts API key set"}

    try:
        body = _post(BALANCE_ENDPOINT, {"api_key": api_key}, timeout=10)
    except Exception as exc:
        return {"ok": False, "balance": None, "error": str(exc)}

    code = str(body.get("status_code", "")).strip()
    if code != SUCCESS:
        return {
            "ok": False,
            "balance": None,
            "error": STATUS_MEANINGS.get(code, "Unknown error"),
        }

    try:
        balance = int(float(body.get("balance", 0)))
    except (TypeError, ValueError):
        balance = None
    return {"ok": True, "balance": balance, "error": None}


def _flag_if_serious(entry, tenant):
    """
    Escalate the failures retrying will never fix.

    A bad number concerns one customer. An empty account or a rejected key
    concerns every message the operator sends until somebody acts, so it goes to
    them rather than into a log.
    """
    if not isinstance(entry, dict):
        return
    code = str(entry.get("status_code", "")).strip()
    if code not in NEEDS_ATTENTION:
        return

    detail = (
        f"SMS is not going out: {STATUS_MEANINGS.get(code, 'error ' + code)}. "
        "Customers are not receiving expiry warnings or their voucher codes."
    )
    logger.error("[sms] %s", detail)
    try:
        from billing.tasks.alert_tasks import notify_admin_task

        notify_admin_task.delay(detail)
    except Exception:
        # An alert about SMS that itself travels by SMS. Best effort by design.
        logger.exception("[sms] could not raise the alert")


# ─── WhatsApp ────────────────────────────────────────────────────────────────

def send_whatsapp(phone: str, message: str, tenant=None) -> bool:
    """
    Send WhatsApp message via Meta Cloud API.
    Phone must be in international format (e.g. 2547XXXXXXXX).
    API version is configurable via SystemSetting WHATSAPP_API_VERSION.
    """
    token = get_setting("WHATSAPP_TOKEN", tenant=tenant)
    phone_id = get_setting("WHATSAPP_PHONE_ID", tenant=tenant)
    version = get_setting("WHATSAPP_API_VERSION", tenant=tenant) or _DEFAULT_WA_VERSION

    if not token or not phone_id:
        logger.warning("[whatsapp] Skipped — missing WhatsApp credentials in SystemSetting")
        return False

    # The docstring above has always said international format; nothing made it
    # so. Same defect as the SMS path and it matters more here, because
    # notify_customer falls back from one to the other — a number stored as
    # 0712345678 failed both channels and the customer heard nothing at all.
    phone = normalise_phone(phone)

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


def notify_customer(phone: str, message: str, tenant=None) -> None:
    """
    Notify via both SMS and WhatsApp. Failure in one channel does not stop
    the other. Designed to be called synchronously (e.g. from model.save()).
    For async delivery use notify_customer_task instead.
    """
    sms_ok = send_sms(phone, message, tenant=tenant)
    wa_ok = send_whatsapp(phone, message, tenant=tenant)

    if not sms_ok and not wa_ok:
        logger.warning(f"[notify] Both SMS and WhatsApp failed for {phone}")
