from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class HotspotPublicThrottle(AnonRateThrottle):
    scope = "hotspot_public"


class MpesaCallbackThrottle(AnonRateThrottle):
    """
    Higher limit — Safaricom can retry callbacks multiple times.
    Restricts by IP to block non-Safaricom flood attempts.
    """
    scope = "mpesa_callback"


class STKPushThrottle(UserRateThrottle):
    scope = "stk_push"
