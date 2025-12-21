from __future__ import annotations

from typing import Optional

from django.utils import timezone
from django.db import transaction

from billing.models import Voucher, Payment, Subscription


# If your Subscription has a "revoked" or "cancelled" state, block it here.
# Safe default: expiry_date is the main source of truth.
BLOCKED_SUB_STATUSES = {"revoked", "cancelled"}


def _normalize_code(code: str) -> str:
    """
    Normalize user input to reduce false negatives.
    - strips spaces
    - keeps case as-is (Mpesa receipts can be case-sensitive sometimes)
    """
    return (code or "").strip()


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


def validate_voucher(code: str, mac_address: Optional[str] = None) -> Optional[Subscription]:
    """
    Validate hotspot access token.

    Accepts:
    1) Voucher.code (Voucher model)
    2) M-Pesa receipt (Payment.reference) — ONLY if payment.method == "mpesa"

    Returns:
        Subscription if valid, else None

    IMPORTANT:
    - This function DOES NOT bind MAC address.
      Your view should bind MAC if subscription is returned and customer.hotspot_username is empty.
    """
    now = timezone.now()
    code = _normalize_code(code)
    if not code:
        return None

    # ---------------------------------------------------------
    # 1) Try Voucher.code
    # ---------------------------------------------------------
    voucher = (
        Voucher.objects
        .select_related("subscription", "subscription__customer")
        .filter(code=code)
        .first()
    )

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
    payment = (
        Payment.objects
        .select_related("subscription", "subscription__customer")
        .filter(reference=code)
        .order_by("-paid_at", "-id")
        .first()
    )

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
