"""
Getting a paying customer onto the network, and not giving up quietly.

The gap this closes: after a payment, access was granted inline and once. If no
router answered at that moment — a reboot, a power cut, a link down for ninety
seconds — the attempt logged a warning and returned. The invoice was paid, the
subscription active, and the customer had an SMS saying their account was ready.
Nothing retried, and nobody was told.

A router being briefly unreachable is ordinary. Somebody paying and getting
nothing is not, so it retries, and if it still cannot it says so to the operator
rather than to a log file nobody reads.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

# How long to wait before each retry, in seconds.
#
# This was 60s, 240s, 960s -- one minute, four, sixteen. The outer end of that
# is right and is kept: it rides out a reboot or a power cut, and skylink was
# observed restarting mid-evening with every hotspot session cleared.
#
# The near end was wrong, and wrong in the case that actually happens. A first
# attempt does not only fail because a router is down; it fails because the
# link to it dropped a packet. skylink3 reaches the internet over 5G measured
# at 33% loss and 600ms round trip, and an API login is several round trips --
# so the first attempt fails routinely against a router that is sitting there
# working, and answers a few seconds later.
#
# Against that, a 60-second first retry is a minute of a customer standing at
# a hotspot having paid, associated to the Wi-Fi, and holding no authorised
# session: connected, no internet, which is precisely the complaint this was
# traced from. Two fast attempts cost one worker slot for a few seconds and
# recover the common case in about five.
#
# The last attempt still lands sixteen minutes out, so nothing that used to be
# ridden out stops being ridden out.
RETRY_SCHEDULE = (5, 20, 60, 240, 960)
MAX_ATTEMPTS = len(RETRY_SCHEDULE) + 1


@shared_task(bind=True, max_retries=MAX_ATTEMPTS - 1)
def ensure_customer_access_task(self, customer_id, reason="payment"):
    """
    Provision a customer, retrying while the network is uncooperative.

    Idempotent: enable_customer_access creates or updates the secret rather than
    assuming it is absent, so running it twice is harmless. That matters,
    because a retry cannot know whether the previous attempt half-succeeded.
    """
    from billing.models import AccessAuditLog, Customer
    from billing.router_service import enable_customer_access
    from billing.tenancy import tenant_context

    customer = (
        Customer.objects.all_tenants().select_related("router", "tenant")
        .filter(id=customer_id).first()
    )
    if customer is None:
        logger.warning("[provisioning] customer %s no longer exists", customer_id)
        return False

    with tenant_context(customer.tenant_id):
        try:
            granted = enable_customer_access(customer)
        except Exception as exc:
            granted = False
            logger.warning(
                "[provisioning] attempt %s for %s raised: %s",
                self.request.retries + 1, customer, exc,
            )

        if granted:
            if self.request.retries:
                logger.info(
                    "[provisioning] %s online after %s attempt(s)",
                    customer, self.request.retries + 1,
                )
            return True

        if self.request.retries < self.max_retries:
            # Indexed rather than computed, so the schedule reads as the list
            # of waits it is and cannot drift from MAX_ATTEMPTS.
            countdown = RETRY_SCHEDULE[self.request.retries]
            logger.warning(
                "[provisioning] no router for %s, retrying in %ss", customer, countdown)
            raise self.retry(countdown=countdown)

        # Out of attempts. The customer has paid and has nothing, so this stops
        # being a network event and becomes something a person must handle.
        _report_failure(customer, reason)
        return False


def _report_failure(customer, reason):
    from billing.models import AccessAuditLog
    from billing.tasks.alert_tasks import notify_admin_task

    detail = (
        f"Could not put {customer.full_name} ({customer.phone}) on the network "
        f"after {MAX_ATTEMPTS} attempts — no router was reachable. They have "
        f"paid. Trigger: {reason}."
    )
    logger.error("[provisioning] %s", detail)

    # On the customer's own record, because that is where anyone investigating
    # this particular person will look.
    try:
        AccessAuditLog.objects.create(
            customer=customer, action="provisioning_failed", reason=detail)
    except Exception:
        logger.exception("[provisioning] could not record the failure for %s", customer)

    # And to the operator, because nobody watches a log.
    try:
        notify_admin_task.delay(detail)
    except Exception:
        logger.exception("[provisioning] could not alert the operator for %s", customer)
