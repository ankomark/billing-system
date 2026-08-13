from django.conf import settings


def client_ip(request):
    """
    Who the request came from, as far as we can tell.

    Trusts the first X-Forwarded-For hop because a reverse proxy sits in front
    in production. That is only sound while the proxy is the sole route in —
    the app port is bound to loopback, which is what makes it so.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def is_trusted_mpesa_ip(request):
    """
    Whether this callback came from an address Safaricom is known to use.

    A fast path, not a gate. Safaricom publishes more addresses than any list
    here tends to hold and changes them without notice, so treating this as the
    only authenticator means a genuine callback from an unlisted address is
    dropped — and the customer has already been charged by then. See
    MpesaSTKCallbackView for what happens when this returns False.
    """
    ip = client_ip(request)

    # Allow localhost during development
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


# =====================================================
# HOTSPOT DEVICE TOKEN
# =====================================================

def device_token_for(mac_address: str) -> str:
    """
    A secret held by a device that has proved it belongs to the account.

    /hotspot/status/ answers on a MAC address supplied by the caller, and over
    plain http on a shared network nothing can verify that the caller is that
    device: the router knows, the API cannot. MAC addresses of everyone else on
    the WiFi are a network-scanner app away, so asking for a stranger's status
    returned their voucher code — a credential — along with their package and
    what they had used.

    Issued by exactly one thing: redeeming a working code. That is the only
    step on the public surface where a caller demonstrates anything, and the
    portal keeps what it is given and presents it back. Reconnect looks like
    a candidate and is not — it takes the MAC on the caller's word too, so
    issuing proof there would hand it to anyone who named a stranger's
    address.

    Derived rather than stored, like the poll token: an HMAC over the MAC needs
    no column, no cleanup, and cannot be read out of the database.

    Taken over the canonical address, not over what the caller typed.
    Upper-casing alone folded case but not separators, so the token issued at
    redemption and the token checked at /hotspot/status/ agreed only while both
    callers happened to write the address the same way — and the failure is
    silent: the status endpoint simply withholds the voucher code from the
    device that earned it.
    """
    import hashlib
    import hmac

    from django.conf import settings

    from .utils import normalize_mac

    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"hotspot-device:{normalize_mac(mac_address)}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def device_token_matches(mac_address: str, supplied) -> bool:
    """Constant-time, so a wrong token leaks nothing by how long it took."""
    import hmac

    if not supplied or not mac_address:
        return False
    return hmac.compare_digest(device_token_for(mac_address), str(supplied))
