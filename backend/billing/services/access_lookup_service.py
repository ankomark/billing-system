from django.utils import timezone
from billing.models import Voucher, Payment


def lookup_access_token(code: str):
    """
    Admin lookup for voucher OR M-Pesa receipt.
    Returns a normalized dict or None.
    """

    # 1️⃣ Try Voucher
    #
    # Case-insensitive, like redemption and deactivation. This is the tool an
    # operator opens while a customer reads a code down the phone, and codes
    # are minted from an uppercase alphabet — so two cannot differ by case,
    # and an exact match only ever turned a code that exists into "not found".
    voucher = (
        Voucher.objects
        .select_related("subscription", "subscription__customer", "subscription__package")
        .filter(code__iexact=code)
        .first()
    )

    if voucher:
        sub = voucher.subscription
        pkg = sub.package
        return {
            "type": "voucher",
            "code": voucher.code,
            "customer": sub.customer,
            "package": pkg.name,
            "duration": f"{pkg.duration_value} {pkg.duration_unit}",
            "created_at": voucher.created_at,
            "expires_at": voucher.expires_at,
            "is_active": voucher.is_active,
            "subscription_status": sub.status,
            "mac_address": sub.customer.hotspot_username,
        }

    # 2️⃣ Try M-Pesa receipt
    payment = (
        Payment.objects
        .select_related("subscription", "subscription__customer", "subscription__package")
        .filter(reference__iexact=code, method="mpesa")
        .first()
    )

    if payment:
        sub = payment.subscription
        pkg = sub.package
        return {
            "type": "mpesa",
            "code": payment.reference,
            "customer": sub.customer,
            "package": pkg.name,
            "duration": f"{pkg.duration_value} {pkg.duration_unit}",
            "created_at": payment.paid_at,
            "expires_at": sub.expiry_date,
            # Payment has no is_revoked field — reading it raised AttributeError.
            # For a receipt-as-token lookup, "active" means the subscription it
            # paid for has not yet expired.
            "is_active": bool(sub.expiry_date and sub.expiry_date > timezone.now()),
            "subscription_status": sub.status,
            "mac_address": sub.customer.hotspot_username,
        }

    return None
