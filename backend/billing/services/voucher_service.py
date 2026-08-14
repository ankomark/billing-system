from __future__ import annotations

import logging
import re
from typing import List, Optional

from django.utils import timezone
from django.db import transaction

from billing.models import Voucher, Payment, Subscription
from billing.utils import normalize_mac

logger = logging.getLogger(__name__)


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

# Voucher codes issued before the prefix was dropped, e.g. WIFI-NITGYQ. Still
# matched because those vouchers are still out there on customers' phones.
_VOUCHER_RE = re.compile(r"\b[A-Z]{2,10}-[A-Z0-9]{4,12}\b")

# The code as it appears in our own SMS: "Voucher Code: QWIALE".
#
# Anchored on the label for the same reason _CONFIRMED_RE is anchored on
# "Confirmed": without it there is nothing to distinguish a six-character code
# from six-character English. Uppercasing the message turns half of it into
# things that look like codes — the welcome SMS alone yields AUTO-LOGIN,
# UNLIMITED and CONNECTED, which is MAX_CANDIDATES exhausted before the real
# code is even considered. Tried first, so the one token we can be certain
# about takes the first slot.
#
# "CODE" is optional, and "is" counts as a separator, because the wording is
# the operator's now — see message_templates. Requiring the exact phrase
# "Voucher Code:" meant our own default, which reads "Voucher: QWIALE", did
# not anchor: UNLIMITED, the support number and the brand took all three
# candidate slots and the code was never tried at all. A customer pasting the
# SMS we sent them was told it was invalid.
# Separators repeat, because people write what reads well rather than what
# parses well: an operator's live template says "Your voucher is : QWIALE",
# with both the word and the colon. One separator matched IS, then met the
# colon where it wanted the code, and gave up.
_VOUCHER_LABEL_RE = re.compile(
    r"VOUCHER\s*(?:CODE)?\s*(?:(?:IS\b|[:\-])\s*)+([A-Z0-9][A-Z0-9-]{3,19})",
    re.IGNORECASE,
)

# Voucher codes as issued now: six characters, no prefix.
#
# Tried last, because it is the loosest — six uppercase alphanumerics also
# describe plenty of ordinary words. It is the fallback for a customer who
# pastes something other than our SMS; the label above is what handles ours.
_BARE_CODE_RE = re.compile(r"\b[A-Z0-9]{6}\b")

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
    # Digits and nothing else, held back until last. Our own messages carry a
    # support number, and a ten-digit phone number is exactly the shape of an
    # M-Pesa receipt, so it was taking a candidate slot off the real code.
    #
    # Held rather than dropped: a code is six characters from an alphabet that
    # includes digits, so roughly one in two thousand is all digits, and
    # discarding those would fail a customer with no way to explain why.
    digits_only: List[str] = []

    def add(value, labelled=False):
        """
        `labelled` where the message named this as the code, rather than it
        merely being code-shaped. That is evidence, so it keeps its place even
        if it is all digits — a code named as the code is the code.
        """
        value = value.strip()
        if not value or value.upper() in _NOT_A_CODE:
            return
        if value in candidates or value in digits_only:
            return
        if value.isdigit() and not labelled:
            digits_only.append(value)
        else:
            candidates.append(value)

    # Our own SMS names the code, so that reading beats every guess below it.
    labelled = _VOUCHER_LABEL_RE.search(text)
    if labelled:
        add(labelled.group(1).upper(), labelled=True)

    confirmed = _CONFIRMED_RE.search(text)
    if confirmed:
        add(confirmed.group(1).upper(), labelled=True)

    for match in _VOUCHER_RE.finditer(upper):
        add(match.group(0))

    for match in _RECEIPT_RE.finditer(upper):
        add(match.group(0))

    # Last, and only if there is room left. The welcome SMS reads "Voucher
    # Code: QWIALE" inside a paragraph of text, and people paste the whole
    # message rather than picking the code out of it.
    for match in _BARE_CODE_RE.finditer(upper):
        add(match.group(0))

    return (candidates + digits_only)[:MAX_CANDIDATES]


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
    Whether this device may use this subscription at all.

    Not the device *limit* — that lives in the redemption view, which can tell
    a customer how many devices they have used and how many they bought. This
    is the cruder question of whether the address is already known to belong to
    this subscriber, and it exists for callers that have no better answer.

    The hotspot redemption endpoint deliberately does not pass a MAC here, and
    should not start: refusing there returns "Invalid or expired voucher",
    which is a lie to somebody whose second phone is perfectly entitled to
    connect. It calls this with the code alone and does the device accounting
    itself.

    It used to compare against `customer.hotspot_username` only — the *first*
    device. Any second device on a multi-device package was therefore refused
    outright, so the moment a caller did pass a MAC, `max_devices` became 1.
    """
    if not mac_address:
        return True

    customer = getattr(sub, "customer", None)
    if not customer:
        return False

    from billing.models import CustomerDevice

    # Canonical on both sides. Comparing what the caller happened to send
    # against what the writer happened to store is how the same phone reads as
    # a stranger's.
    incoming = normalize_mac(mac_address)
    if not incoming:
        return True

    known = set()
    legacy = normalize_mac(customer.hotspot_username)
    if legacy:
        known.add(legacy)
    known.update(
        normalize_mac(m)
        for m in CustomerDevice.objects.all_tenants()
        .filter(tenant_id=customer.tenant_id, customer=customer, blocked=False)
        .values_list("mac_address", flat=True)
        if m
    )

    # Nothing bound yet: the caller binds after validating.
    if not known:
        return True

    return incoming in known


def mark_voucher_used(subscription, mac_address: Optional[str] = None) -> None:
    """
    Stamp when a code was first redeemed, and by what.

    `Voucher.bound_mac` says "MAC address bound on first use" in its own
    help_text and `first_used_at` is named for the same event. Neither was ever
    written — they have been null on every row since the model was added. An
    operator asking "when was this code first used, and by whom" had the
    columns for it in the admin and nothing in them.

    Only the first use is recorded; a later device on a multi-device package
    does not overwrite it. Failure is swallowed on purpose — this is a record,
    and losing it must not cost a paying customer the connection they just
    redeemed a working code for.
    """
    try:
        stamped = timezone.now()
        for voucher in Voucher.objects.all_tenants().filter(
            subscription=subscription, first_used_at__isnull=True
        ):
            voucher.first_used_at = stamped
            if mac_address and not (voucher.bound_mac or "").strip():
                voucher.bound_mac = normalize_mac(mac_address)
            voucher.save(update_fields=["first_used_at", "bound_mac"])
    except Exception:
        logger.exception(
            "[voucher] could not record first use for subscription %s",
            getattr(subscription, "pk", subscription),
        )


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


# What a refusal was actually about. `validate_voucher` answers yes or no,
# which is all the network needs and less than the person standing at the
# portal needs.
REFUSED_UNKNOWN = "unknown"
REFUSED_EXPIRED = "expired"


def describe_refusal(code: str, tenant=None) -> str:
    """
    Why a code that did not validate did not validate.

    Every refusal above returns None, and the portal turns every None into
    "Invalid or expired voucher" — one sentence covering two situations that
    have nothing in common. A code that never existed is a typo, and retyping
    it is the right thing to do. A code whose time ran out is a customer who
    paid, got what they paid for, and now needs to buy again; telling them it
    is invalid sends them to retype a code that will never work, and sends the
    operator a complaint that their codes are broken.

    Only the refusal path calls this, so the cost lands on the answer nobody
    is waiting on rather than on every redemption.

    Returns REFUSED_EXPIRED when the code is genuinely this operator's and the
    only thing wrong with it is the clock, and REFUSED_UNKNOWN otherwise —
    including for a code held back for any other reason, because a customer
    should never be told a code has expired when it has not.
    """
    now = timezone.now()

    for candidate in extract_codes(_normalize_code(code)):
        vouchers = Voucher.objects.all_tenants().select_related(
            "subscription").filter(code__iexact=candidate)
        payments = Payment.objects.all_tenants().select_related(
            "subscription").filter(reference__iexact=candidate, method="mpesa")
        if tenant is not None:
            vouchers = vouchers.filter(tenant=tenant)
            payments = payments.filter(tenant=tenant)

        voucher = vouchers.first()
        if voucher is not None:
            if voucher.expires_at and voucher.expires_at <= now:
                return REFUSED_EXPIRED
            sub = voucher.subscription
            if sub and sub.expiry_date and sub.expiry_date <= now:
                return REFUSED_EXPIRED
            continue

        payment = payments.order_by("-paid_at", "-id").first()
        if payment is not None:
            sub = payment.subscription
            if sub and sub.expiry_date and sub.expiry_date <= now:
                return REFUSED_EXPIRED

    return REFUSED_UNKNOWN


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
    # Case-insensitive, because the people typing these are on phones.
    #
    # Codes are minted uppercase (WIFI-QWIALE) and matched exactly, so anything
    # a keyboard does to them is a rejection. Android auto-capitalises the
    # first letter of a text field and lowercases the rest, which turns that
    # code into "Wifi-qwiale" without the customer touching anything — and the
    # answer they get is "Invalid or expired voucher", about a voucher they
    # paid for thirty seconds ago.
    #
    # Confirmed against a live redemption: the same voucher validated as
    # WIFI-QWIALE and was refused as Wifi-qwiale. There is nothing to lose by
    # folding case here; the codes are generated from a fixed uppercase
    # alphabet, so two that differ only in case cannot exist.
    voucher = (
        Voucher.objects.all_tenants()
        .select_related("subscription", "subscription__customer")
        .filter(code__iexact=code)
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
        # Same reasoning as the voucher lookup above. Safaricom receipts are
        # uppercase alphanumerics, so folding case cannot merge two distinct
        # references — and a customer who types theirs rather than pasting the
        # message hits exactly the same keyboard behaviour.
        .filter(reference__iexact=code)
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
