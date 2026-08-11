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
# so these are the difference between "sent" and "silently discarded". Some
# refusals arrive as an HTTP error instead, carrying the same body — see _post.
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
    # "Low bulk credits" in the provider's own wording. Not "out of" — the
    # account can still have some, and an alert that says it is empty when it
    # is merely low sends an operator looking for the wrong thing.
    "1009": "Low SMS credit",
}

# The failures no amount of retrying fixes, and which affect every message
# rather than one. An empty account or a rejected key needs a person.
NEEDS_ATTENTION = {"1001", "1002", "1003", "1004", "1009"}

# Stands in for a status code where the operator has no BlessedTexts account
# configured at all, so nothing was ever asked and there is no code to report.
# A verdict all the same, and a permanent one: see send_sms_result.
UNCONFIGURED = "unconfigured"


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


# ─── The record an operator can actually read ────────────────────────────────

def _record(channel, phone, status, tenant, *, code="", reason="", message=""):
    """
    Write one row of the delivery log.

    Everything below used to end at logger.error, which is a file on a server
    an operator cannot read and would not know to ask for. M-Pesa has had a
    table and a page since its callback was written; this is the same thing for
    messages. See MessageLog.

    Never raises. A log row is worth having and is worth nothing at all if
    failing to write one can stop a message going out, or turn a refusal the
    caller was handling into an exception it was not expecting.
    """
    try:
        from billing.config import _resolve_tenant_id
        from billing.models import MessageLog

        tenant_id = _resolve_tenant_id(tenant)
        fields = dict(
            channel=channel,
            phone=phone or "",
            status=status,
            status_code=code or "",
            reason=reason or "",
            message=message or "",
        )
        # Only when we know it. Left out, the model's default_tenant applies,
        # which reads the tenant in context and is the right answer inside a
        # request or a tenant_context() block.
        if tenant_id is not None:
            fields["tenant_id"] = tenant_id

        MessageLog.objects.create(**fields)
    except Exception:
        logger.exception("[notify] could not record the delivery attempt")


def _record_many(rows, tenant):
    """
    The same, for a broadcast, in one insert rather than one per recipient.

    A broadcast to four hundred subscribers is one request to the provider —
    deliberately, see send_bulk_sms — and it would be a poor trade to answer
    that with four hundred INSERTs.

    Never raises, for the same reason _record does not.
    """
    if not rows:
        return
    try:
        from billing.config import _resolve_tenant_id
        from billing.models import MessageLog

        tenant_id = _resolve_tenant_id(tenant)
        common = {"tenant_id": tenant_id} if tenant_id is not None else {}
        MessageLog.objects.bulk_create([
            MessageLog(
                channel=row.get("channel", "sms"),
                phone=row.get("phone") or "",
                status=row["status"],
                status_code=row.get("code") or "",
                reason=row.get("reason") or "",
                message=row.get("message") or "",
                **common,
            )
            for row in rows
        ])
    except Exception:
        logger.exception("[notify] could not record %s delivery attempts", len(rows))


def _credentials(tenant=None):
    """
    The operator's API key and sender ID, with surrounding whitespace removed.

    Both are pasted into a settings form by hand. A trailing space or a newline
    picked up from a copy goes on the wire intact, comes back 1002 or 1004, and
    is indistinguishable on the settings page from a value that is simply
    wrong — the field looks right because it *is* right, apart from the part
    that does not render.

    We have already lost a day to a sender ID whose reason was being discarded.
    Losing another to one that only looks correct would be worse.

    A value that is nothing but whitespace strips to empty and is then treated
    as unset, which is what it is: the caller declines to send rather than
    asking the provider about a blank sender ID.
    """
    def clean(value):
        return value.strip() if isinstance(value, str) else value

    return (
        clean(get_setting("BLESSEDTEXTS_API_KEY", tenant=tenant)),
        clean(get_setting("BLESSEDTEXTS_SENDER_ID", tenant=tenant)),
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


def _carries_provider_status(body):
    """Whether a parsed body is BlessedTexts explaining itself."""
    entry = body[0] if isinstance(body, list) and body else body
    return isinstance(entry, dict) and "status_code" in entry


def _post(url, payload, timeout=15):
    """
    Post, and keep the provider's own account of what went wrong.

    This called raise_for_status() and nothing else, on the belief — stated in
    send_sms below, and true of every refusal the tests covered — that this
    provider answers HTTP 200 and puts the reason in the body. It answers 200
    for some of them. An invalid sender ID comes back as **HTTP 422**, with a
    body in exactly the same shape:

        {"status_code": "1004", "status_desc": "Invalid Sender ID: Blessed"}

    raise_for_status() turned that into an exception and discarded the body, so
    the caller logged "Request to 2547... failed: 422 Client Error" and stopped.
    1004 is in STATUS_MEANINGS and in NEEDS_ATTENTION, so it should have been
    named and should have raised an alert — every message an operator sends is
    failing and only they can fix it. Neither happened, because the code that
    does both sits after this function returns.

    An operator lost a day to that: a valid key, a readable credit balance, and
    every send failing with nothing anywhere saying the sender ID was the
    reason.

    So a body that carries a status_code is returned however the request was
    answered, and _describe reads it. A genuine transport failure — a 502 from
    a proxy, an HTML error page, a timeout — has no such body and still raises.
    """
    response = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=timeout,
    )

    if not response.ok:
        try:
            body = response.json()
        except ValueError:
            body = None
        if _carries_provider_status(body):
            return body

    response.raise_for_status()
    return response.json()


def send_sms_result(phone: str, message: str, tenant=None):
    """
    Send one SMS through BlessedTexts, and report what the provider said.

    Returns (accepted, code): the status code the provider answered with, or
    None where it never gave one — a timeout, a 502 from a proxy, an
    unreadable body. UNCONFIGURED where the operator has no account set up, so
    nothing was asked in the first place.

    That None is the whole reason this exists alongside send_sms(). A bool
    cannot tell a refusal from a failure to ask, and send_sms_task retried both
    five times: an invalid sender ID rejects every message an operator sends,
    and the old task answered by sending each one six times. The provider
    having given a verdict means retrying cannot change it.
    """
    api_key, sender_id = _credentials(tenant)
    if not api_key or not sender_id:
        logger.warning("[sms] Skipped — BlessedTexts is not set up for this operator")
        _record("sms", phone, "failed", tenant, code=UNCONFIGURED, message=message,
                reason="SMS is not set up for this operator — no BlessedTexts "
                       "API key or sender ID has been saved")
        return False, UNCONFIGURED

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
        _record("sms", phone, "failed", tenant, reason=str(exc), message=message)
        return False, None

    # A send answers with a list; a malformed request can answer with a bare
    # object. Both shapes are handled rather than assumed.
    entries = body if isinstance(body, list) else [body]
    if not entries:
        logger.error("[sms] Empty response for %s", phone)
        _record("sms", phone, "failed", tenant, message=message,
                reason="The provider answered with nothing at all")
        return False, None

    entry = entries[0]
    ok, reason = _describe(entry)
    if ok:
        logger.info("[sms] Sent to %s", phone)
    else:
        logger.error("[sms] Refused for %s: %s", phone, reason)
        _flag_if_serious(entry, tenant)

    code = str(entry.get("status_code", "")).strip() if isinstance(entry, dict) else ""
    _record("sms", phone, "sent" if ok else "refused", tenant,
            code=code, reason="" if ok else reason, message=message)
    return ok, code or None


def send_sms(phone: str, message: str, tenant=None) -> bool:
    """
    Send one SMS through BlessedTexts, on the operator's own account.

    Returns True only when the provider accepted it.

    That distinction is the whole point of this rewrite. The previous version
    called raise_for_status() and returned True — but this provider answers HTTP
    200 for a refusal and puts the reason in the body, so a message rejected for
    an empty account or a bad number was reported as sent. An expiry warning
    nobody received, recorded as delivered, is worse than a visible failure.

    Use send_sms_result() where the difference between "refused" and "could not
    be asked" matters, which is anywhere a retry might follow.
    """
    ok, _code = send_sms_result(phone, message, tenant=tenant)
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
        reason = ("SMS is not set up for this operator — no BlessedTexts API "
                  "key or sender ID has been saved")
        _record_many([
            {"phone": phone, "status": "failed", "code": UNCONFIGURED,
             "reason": reason, "message": message}
            for phone, message in pairs
        ], tenant)
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
        _record_many([
            {"phone": phone, "status": "failed", "reason": str(exc),
             "message": message}
            for phone, message in pairs
        ], tenant)
        return {"sent": 0, "failed": len(pairs), "errors": [str(exc)]}

    entries = body if isinstance(body, list) else [body]
    sent = failed = 0
    errors = []
    rows = []
    for index, entry in enumerate(entries):
        ok, reason = _describe(entry)
        # The provider answers in the order it was asked, so the pair at this
        # index is whose message this is. Its own phone field is preferred
        # where it gave one, since that is the number it actually acted on.
        given_phone, given_message = pairs[index] if index < len(pairs) else ("", "")
        phone = entry.get("phone") if isinstance(entry, dict) else None

        if ok:
            sent += 1
        else:
            failed += 1
            phone = phone or given_phone or "unknown"
            errors.append(f"{phone}: {reason}")
            _flag_if_serious(entry, tenant)

        rows.append({
            "phone": phone or given_phone,
            "status": "sent" if ok else "refused",
            "code": str(entry.get("status_code", "")).strip() if isinstance(entry, dict) else "",
            "reason": "" if ok else reason,
            "message": given_message,
        })

    # One row comes back per recipient the provider handled. Anyone it did not
    # answer for was not sent, and counting them as failed beats implying they
    # went out.
    unanswered = len(pairs) - len(entries)
    if unanswered > 0:
        failed += unanswered
        errors.append(f"{unanswered} recipient(s) got no response from the provider")
        # Named individually rather than as a count, because "four hundred sent,
        # three failed" is useless to whoever has to work out which three.
        rows.extend([
            {"phone": phone, "status": "failed", "message": message,
             "reason": "The provider returned no result for this recipient"}
            for phone, message in pairs[len(entries):]
        ])

    _record_many(rows, tenant)

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

    # This read body["status_code"] directly, on the documented shape — an
    # object. _post now returns whatever carries a status_code, and a refusal
    # can carry it inside a list, so a bare .get() was an AttributeError raised
    # out of a settings page that was only asking how much credit was left.
    entry = body[0] if isinstance(body, list) and body else body

    # _describe rather than a second copy of the same lookup, which had already
    # drifted: it threw away the provider's status_desc and answered "Unknown
    # error" for any code not in our table — the very mistake the send path was
    # fixed for.
    ok, reason = _describe(entry)
    if not ok:
        return {"ok": False, "balance": None, "error": reason}

    try:
        balance = int(float(entry.get("balance", 0)))
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
        _record("whatsapp", phone, "failed", tenant, message=message,
                reason="WhatsApp is not set up for this operator — no token or "
                       "phone number ID has been saved")
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
        _record("whatsapp", phone, "sent", tenant, message=message)
        return True
    except Exception as exc:
        logger.error(f"[whatsapp] Failed to {phone}: {exc}")
        # Meta issues no status code of the kind BlessedTexts does, so the
        # exception text is the whole of what it said. Usually an HTTP status
        # and, for the one that matters most, "401 Unauthorized" — an expired
        # token, which fails every message the same way a bad sender ID does.
        _record("whatsapp", phone, "failed", tenant, reason=str(exc), message=message)
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
