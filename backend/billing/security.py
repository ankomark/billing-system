from django.conf import settings


def is_trusted_mpesa_ip(request):
    """
    Validates that the request IP belongs to Safaricom
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR")

    # ✅ Allow localhost during development
    if settings.MPESA_ALLOW_LOCAL_CALLBACK and ip in ("127.0.0.1", "localhost"):
        return True

    return ip in settings.MPESA_TRUSTED_IPS


# =====================================================
# HOTSPOT PURCHASE POLL TOKEN
# =====================================================

def poll_token_for(invoice_number: str) -> str:
    """
    A secret the purchaser holds, proving a poll belongs to them.

    /hotspot/payment-status/ returns the voucher code once an invoice is paid,
    and it was addressed by invoice number alone. Those look like
    INV-20260801191649-1338: a second-resolution timestamp and four hex
    characters, so a five-minute window is about 20 million combinations. Not
    guessable by hand, but nothing about it is secret either — the only thing
    standing between a stranger and somebody else's voucher was the rate limit,
    and a rate limit is a cost, not a boundary.

    Derived rather than stored: an HMAC over the invoice number needs no
    column, no migration and no cleanup, and cannot be read out of the database
    by anything that gets a look at an invoice.
    """
    import hashlib
    import hmac

    from django.conf import settings

    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"hotspot-poll:{invoice_number}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def poll_token_matches(invoice_number: str, supplied) -> bool:
    """Constant-time, so a wrong token leaks nothing by how long it took."""
    import hmac

    if not supplied or not invoice_number:
        return False
    return hmac.compare_digest(poll_token_for(invoice_number), str(supplied))
