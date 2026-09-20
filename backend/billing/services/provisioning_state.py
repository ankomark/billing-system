"""
Whether a subscriber's account has actually reached the router yet.

The captive portal needs this one answer and nothing else. When redemption
stops waiting for the router -- see HotspotVoucherValidateView -- the portal is
left holding a valid code and no account to log in with, and the only thing it
can safely do is wait until there is one. Attempting the RouterOS login early
is worse than waiting: the router refuses it as "invalid username or password",
sends the browser to an error page, and tryAutoReconnect refuses to retry after
a router error, so the customer is stuck on a page that will not try again.

Deliberately not a database column. This says "the grant landed a moment ago",
which is transient, per-attempt and read once by the page that was waiting on
it -- a row would outlive its meaning and need pruning, and a migration would
have to ship before the endpoint that sets it. The cache is the right shape and
the right lifetime.

Nothing depends on it being reliable. A miss reads as "not yet", and the portal
gives up waiting after its own deadline and tries the login anyway, which is
exactly what it does today. So an evicted key, a cold cache or a Redis outage
costs a customer the wait they would have had before this existed, and never
service they were entitled to.
"""

import logging

from django.core.cache import cache

from billing.utils import normalize_mac

logger = logging.getLogger(__name__)

# Long enough to cover the retry schedule a caller could plausibly be waiting
# through (5s, 20s, 60s, 240s ...), short enough that a key cannot be mistaken
# for the answer to a later question. The portal stops waiting long before it.
TTL_SECONDS = 15 * 60


def _key(tenant_id, mac):
    # Scoped by operator, because a MAC is only unique within one -- the same
    # reason HotspotStatusView takes a tenant token before answering about one.
    return f"provisioned:{tenant_id}:{normalize_mac(mac)}"


def mark_provisioned(tenant_id, macs):
    """
    Record that these addresses now have an account on a router.

    Takes the addresses rather than the customer because that is what the
    portal asks about: one device, standing in front of one radio, wanting to
    know whether it may log in. A customer-wide flag would tell a second handset
    that it was provisioned because the first one was.
    """
    for mac in macs or ():
        if not mac:
            continue
        try:
            cache.set(_key(tenant_id, mac), True, TTL_SECONDS)
        except Exception:
            # A cache that will not write costs the portal its wait, not the
            # customer their connection. Never worth failing a grant over.
            logger.warning(
                "[provisioned] could not record %s for tenant %s",
                mac, tenant_id, exc_info=True)


def is_provisioned(tenant_id, mac):
    """True only when we know the grant landed. Unknown reads as False."""
    if not mac:
        return False
    try:
        return bool(cache.get(_key(tenant_id, mac)))
    except Exception:
        logger.warning("[provisioned] could not read %s for tenant %s",
                       mac, tenant_id, exc_info=True)
        return False


def clear_provisioned(tenant_id, macs):
    """
    Forget it, so the next wait asks the router's state rather than the last
    answer. Used when an account is deliberately taken off a router.
    """
    for mac in macs or ():
        if not mac:
            continue
        try:
            cache.delete(_key(tenant_id, mac))
        except Exception:
            pass
