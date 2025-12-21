from django.utils import timezone
from billing.models import Voucher, Payment


def lookup_access_token(code: str):
    """
    Admin lookup for voucher OR M-Pesa receipt.
    Returns a normalized dict or None.
    """

    # 1️⃣ Try Voucher
    voucher = (
        Voucher.objects
        .select_related("subscription", "subscription__customer", "subscription__package")
        .filter(code=code)
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
        .filter(reference=code, method="mpesa")
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
            "is_active": not payment.is_revoked,
            "subscription_status": sub.status,
            "mac_address": sub.customer.hotspot_username,
        }

    return None
