from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class HotspotPublicThrottle(AnonRateThrottle):
    """
    The endpoints where a stranger could be guessing: redeeming a code, and
    starting a purchase. Keyed by IP on purpose — the whole point is to bound
    how many attempts one source gets, so anything the caller supplies is
    unusable as a key.
    """
    scope = "hotspot_public"


class HotspotPollThrottle(AnonRateThrottle):
    """
    The two endpoints a portal polls: device status, and whether a payment has
    landed.

    These need a much higher ceiling than the guessable endpoints, and keying
    them by IP alone does not work. Every customer of a hotspot sits behind one
    NAT, so the API sees one address for the whole site: a single person
    waiting on an M-Pesa prompt would consume the entire site's allowance and
    everyone else would start getting 429s while their own payments landed.

    So the bucket is per device, or per purchase, whenever the caller has
    something unguessable to identify itself with — a purchase poll token, or
    the MAC of the device it is asking about. Neither is a secret worth
    stealing and neither widens what the endpoint returns. A caller with
    neither falls back to its IP, which is exactly where a guesser belongs.
    """
    scope = "hotspot_poll"

    def get_cache_key(self, request, view):
        token = request.GET.get("token") or request.GET.get("mac")
        if token:
            # Hashed: a MAC is personal data and a cache key is not the place
            # for it, and it bounds key length whatever gets sent.
            import hashlib

            digest = hashlib.sha256(str(token).encode()).hexdigest()[:32]
            return self.cache_format % {"scope": self.scope, "ident": digest}
        return super().get_cache_key(request, view)


class MpesaCallbackThrottle(AnonRateThrottle):
    """
    Higher limit — Safaricom can retry callbacks multiple times.
    Restricts by IP to block non-Safaricom flood attempts.
    """
    scope = "mpesa_callback"


class STKPushThrottle(UserRateThrottle):
    scope = "stk_push"


class RouterTestThrottle(UserRateThrottle):
    """
    Testing router credentials from the registration form.

    The one endpoint where an authenticated operator chooses an address the
    platform's own server then connects to. Nothing about that is unreasonable
    — it is their hardware — but a request that dials an arbitrary host should
    not be available thousands of times a minute, or the form becomes a way to
    survey the network the platform sits in. Keyed per account, and set well
    above what filling in a form takes.
    """
    scope = "router_test"
