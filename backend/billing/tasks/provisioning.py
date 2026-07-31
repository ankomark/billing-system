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

# Roughly one minute, then four, then sixteen. Long enough to ride out a reboot
# or a brief outage; short enough that a customer standing at a hotspot is not
# waiting on the last attempt.
MAX_ATTEMPTS = 4


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
            # 60s, 240s, 960s.
            countdown = 60 * (4 ** self.request.retries)
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
