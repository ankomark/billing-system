from django.utils import timezone
from billing.models import Voucher, Payment


def validate_voucher(code):
    """
    Validate voucher code or M-Pesa receipt.
    Returns Subscription if valid, else None.
    """

    try:
        voucher = Voucher.objects.get(code=code, is_active=True)
        if voucher.expires_at < timezone.now():
            return None
        return voucher.subscription

    except Voucher.DoesNotExist:
        # Fallback: use M-Pesa receipt as voucher
        try:
            payment = Payment.objects.get(reference=code)
            return payment.subscription
        except Payment.DoesNotExist:
            return None
