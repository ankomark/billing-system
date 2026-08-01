from __future__ import annotations

import re
from typing import List, Optional

from django.utils import timezone
from django.db import transaction

from billing.models import Voucher, Payment, Subscription


# If your Subscription has a "revoked" or "cancelled" state, block it here.
# Safe default: expiry_date is the main source of truth.
BLOCKED_SUB_STATUSES = {"revoked", "cancelled"}

# Longest input accepted. An M-Pesa message is around 160 characters; anything
# far beyond that is not one, and parsing it would only widen the search.
MAX_INPUT = 400

# How many candidates one submission may test. This is the real limit on
# guessing: without it, pasting a "message" containing fifty code-shaped tokens
# would turn a single throttled request into fifty attempts.
MAX_CANDIDATES = 3

# Our own voucher codes, e.g. WIFI-NITGYQ.
_VOUCHER_RE = re.compile(r"\b[A-Z]{2,10}-[A-Z0-9]{4,12}\b")

# Safaricom receipts are 10 uppercase alphanumerics and always lead the
# message: "TGX11AA001 Confirmed. Ksh50.00 sent to ...".
_RECEIPT_RE = re.compile(r"\b[A-Z0-9]{8,12}\b")
_CONFIRMED_RE = re.compile(r"\b([A-Z0-9]{8,12})\s+Confirmed", re.IGNORECASE)

# Tokens that match the receipt shape but are words, not codes. Without this
# the first candidate off a pasted message is often "CONFIRMED".
_NOT_A_CODE = {
    "CONFIRMED", "TRANSACTION", "BALANCE", "ACCOUNT", "SUCCESSFUL",
    "RECEIVED", "MPESA", "SAFARICOM", "AVAILABLE",
}


def _normalize_code(code: str) -> str:
    """
    Normalize user input to reduce false negatives.
    - strips spaces
    - keeps case as-is (Mpesa receipts can be case-sensitive sometimes)
    """
    return (code or "").strip()


def extract_codes(text: str) -> List[str]:
    """
    Pull the codes worth trying out of whatever the customer pasted.

    People do not read a receipt code off an SMS and retype it — they long-press
    the message and paste the whole thing. Asking for "just the code" is asking
    them to do the parsing, on a phone, while offline.

    Returns candidates most-likely-first, capped at MAX_CANDIDATES. Order
    matters: the token before "Confirmed" is the receipt in every M-Pesa
    message, so it is tried before anything else code-shaped in the text.
    """
    text = (text or "").strip()
    if not text:
        return []

    # Anything that is already a bare code is used as-is, so a customer typing
    # their voucher in by hand behaves exactly as before.
    if len(text) <= 32 and " " not in text:
        return [text]

    text = text[:MAX_INPUT]
    upper = text.upper()
    candidates: List[str] = []

    def add(value):
        value = value.strip()
        if value and value not in candidates and value.upper() not in _NOT_A_CODE:
            candidates.append(value)

    confirmed = _CONFIRMED_RE.search(text)
    if confirmed:
        add(confirmed.group(1).upper())

    for match in _VOUCHER_RE.finditer(upper):
        add(match.group(0))

    for match in _RECEIPT_RE.finditer(upper):
        add(match.group(0))

    return candidates[:MAX_CANDIDATES]


def _subscription_is_valid_for_access(sub: Subscription) -> bool:
    """
    Subscription validity rules:
    - Must have expiry_date and must be in the future.
    - If status exists and is a blocked status (revoked/cancelled), deny.
    """
    if not sub or not getattr(sub, "expiry_date", None):
        return False

    # Optional status check (only if field exists)
    status = getattr(sub, "status", None)
    if status and status in BLOCKED_SUB_STATUSES:
        return False

    return sub.expiry_date > timezone.now()


def _mac_allowed(sub: Subscription, mac_address: Optional[str]) -> bool:
    """
    MAC rebind protection:
    - If mac_address is given and customer.hotspot_username is already set,
      it must match.
    - If hotspot_username is empty, allow (caller can bind after validation).
    """
    if not mac_address:
        return True

    customer = getattr(sub, "customer", None)
    if not customer:
        return False

    existing = (customer.hotspot_username or "").strip()
    incoming = mac_address.strip()

    # If already bound, must match
    if existing and existing != incoming:
        return False

    return True


def validate_voucher(
    code: str,
    mac_address: Optional[str] = None,
    tenant=None,
) -> Optional[Subscription]:
    """
    Validate hotspot access token.

    Accepts:
    1) Voucher.code (Voucher model)
    2) M-Pesa receipt (Payment.reference) — ONLY if payment.method == "mpesa"
    3) A whole pasted M-Pesa message containing either of the above

    (3) is a convenience with teeth: extract_codes() caps how many candidates
    one submission may test, so a paste cannot be used to smuggle a batch of
    guesses past the endpoint's rate limit.

    A forged message buys nothing. The code is looked up in this operator's own
    Payment and Voucher rows, which only exist because we received and matched
    the callback ourselves — the message is a way of typing a code, never
    evidence that a payment happened.

    Returns:
        Subscription if valid, else None

    `tenant` is which operator's portal the code was presented on, and it is
    not optional in practice. The only caller is a public AllowAny endpoint, so
    no middleware has set a tenant context and these managers run unscoped —
    at the database too, because the RLS policy deliberately allows everything
    when app.current_tenant_id is unset, which is what lets platform staff and
    Celery run cross-tenant.

    The effect, before this argument existed: one operator's voucher validated
    on another operator's captive portal and granted access. Verified against
    the running stack, not theorised — BlueWave's WIFI-7NYLUV came back
    "Access granted" through Skylink's portal and bound a device MAC to a
    BlueWave subscriber.

    Passing None still searches every operator, which is correct for a trusted
    internal caller and wrong for a public one. Callers that cannot establish
    an operator must refuse rather than pass None.

    IMPORTANT:
    - This function DOES NOT bind MAC address.
      Your view should bind MAC if subscription is returned and customer.hotspot_username is empty.
    """
    code = _normalize_code(code)
    if not code:
        return None

    for candidate in extract_codes(code):
        sub = _resolve_code(candidate, mac_address=mac_address, tenant=tenant)
        if sub is not None:
            return sub
    return None


def _resolve_code(
    code: str,
    mac_address: Optional[str] = None,
    tenant=None,
) -> Optional[Subscription]:
    """One exact code, against one operator."""
    now = timezone.now()

    # ---------------------------------------------------------
    # 1) Try Voucher.code
    # ---------------------------------------------------------
    voucher = (
        Voucher.objects.all_tenants()
        .select_related("subscription", "subscription__customer")
        .filter(code=code)
    )
    if tenant is not None:
        voucher = voucher.filter(tenant=tenant)
    voucher = voucher.first()

    if voucher:
        # Must be active
        if not voucher.is_active:
            return None

        # Voucher expiry check
        if voucher.expires_at and voucher.expires_at <= now:
            # Hygiene: auto-deactivate expired vouchers (optional but useful)
            # This avoids a forever-growing list of "active but expired" vouchers.
            try:
                Voucher.objects.filter(pk=voucher.pk, is_active=True).update(is_active=False)
            except Exception:
                pass
            return None

        sub = voucher.subscription
        if not _subscription_is_valid_for_access(sub):
            return None

        if not _mac_allowed(sub, mac_address):
            return None

        return sub

    # ---------------------------------------------------------
    # 2) Fallback: treat M-Pesa receipt as voucher
    # ---------------------------------------------------------
    payments = (
        Payment.objects.all_tenants()
        .select_related("subscription", "subscription__customer")
        .filter(reference=code)
        .order_by("-paid_at", "-id")
    )
    if tenant is not None:
        payments = payments.filter(tenant=tenant)
    payment = payments.first()

    if not payment:
        return None

    # Only allow mpesa receipt codes to act as vouchers
    if getattr(payment, "method", None) != "mpesa":
        return None

    sub = payment.subscription
    if not _subscription_is_valid_for_access(sub):
        return None

    # Optional stricter rule (recommended):
    # If subscription has an invoice and it's not paid, do not grant access.
    invoice = getattr(sub, "invoice", None)
    if invoice and getattr(invoice, "payment_status", None) != "paid":
        return None

    if not _mac_allowed(sub, mac_address):
        return None

    return sub
