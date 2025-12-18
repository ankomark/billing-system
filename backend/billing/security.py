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
