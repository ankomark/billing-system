"""
The sweep that drives hotspot-sharing detection.

Everything about how detection works, and how much it should be trusted, is in
services/tethering.py. This is the clock: which operators have asked for it,
which of their routers to look at, and what to do with cases that have gone
quiet.
"""

import logging

from celery import shared_task
from django.utils import timezone

from billing.services import tethering
from billing.tenancy import all_tenants, tenant_context

logger = logging.getLogger(__name__)


# How long a dispatched per-operator sweep stays worth running.
#
# Matches the expires on the beat entry that fans these out. The router-side
# address-list timeout is set against the five-minute spacing between sweeps —
# see TETHERING_LIST_TIMEOUT — so a sweep that arrives after the next one is
# already due is reasoning about entries that have since aged out.
SWEEP_EXPIRES_SECONDS = 240


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
    retry_jitter=True,
)
def detect_tethering(self):
    """
    Dispatch one sweep per operator, to run in parallel.

    Off for everybody by default, and an operator who has not switched it on
    costs nothing here — no router is dialled and no rule is installed. That is
    deliberate: this writes firewall rules onto hardware somebody else owns, so
    it happens because they asked, not because they upgraded.

    One connection per router, like the usage collectors, for the same reason:
    a sweep that asks per subscriber stops fitting between runs once an
    operator has a few hundred, and Celery drops a late task rather than
    finishing it. Fanning out across operators is the same fix one level up —
    the per-operator cost is a router dialled over a tunnel, so a serial walk
    of every operator runs into the same wall once there are enough of them.
    """
    from billing.models import RouterDevice

    # Which operators own routers at all. Cross-operator by nature — every
    # operator's hardware has to be swept — so it says so.
    with all_tenants():
        tenant_ids = list(
            RouterDevice.objects.all_tenants()
            .filter(is_active=True)
            .values_list("tenant_id", flat=True)
            .distinct()
        )

    for tenant_id in tenant_ids:
        detect_tethering_for_tenant.apply_async(
            (tenant_id,), expires=SWEEP_EXPIRES_SECONDS)

    logger.info("[tether] dispatched sweep for %s operator(s)", len(tenant_ids))
    return len(tenant_ids)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
    retry_jitter=True,
)
def detect_tethering_for_tenant(self, tenant_id):
    """
    Sweep one operator's routers, if they have switched detection on.
    """
    from billing.models import RouterDevice, TetheringCase

    now = timezone.now()
    swept = seen = acted = closed = 0

    # Act as the operator throughout: get_setting() resolves their policy and
    # their message, and notify_customer_task sends through their account.
    # Without this the first operator's settings would decide what happens to
    # everybody.
    with tenant_context(tenant_id):
        if tethering.policy(tenant_id) == tethering.OFF:
            # Switched off, but possibly switched off mid-episode. Anything
            # still applied has to come back off, because nothing will sweep
            # for this operator again — a throttle left behind here is
            # permanent, and by then there is nothing in the logs and nothing
            # on the router that explains it.
            #
            # One cheap query in the ordinary case, where there is nothing
            # open and no router is dialled.
            if TetheringCase.objects.all_tenants().filter(
                    tenant_id=tenant_id,
                    status__in=TetheringCase.OPEN_STATUSES).exists():
                logger.info(
                    "[tether] operator %s has switched detection off — "
                    "closing what is still open", tenant_id)
                closed += tethering.close_stale_cases(
                    tenant_id, now=now, force=True)

            # And the rules that reject traffic, which no case is needed to
            # strand. An operator who ran `block`, caught nobody, and then
            # switched off leaves a router that will cut off the first person
            # to tether — with no sweep to notice, no case, no text, and no
            # policy in force that would explain it. One cached setting
            # decides whether this is worth a connection.
            if tethering.blocks_may_be_installed(tenant_id):
                _lift_blocks(tenant_id)

            # Was a `continue` when this was one iteration of a loop over every
            # operator. It is the whole task now, so falling through here would
            # sweep the routers of an operator who has just been established to
            # have detection switched off.
            return {"routers": 0, "seen": 0, "acted": 0, "closed": closed}

        routers = list(
            RouterDevice.objects.all_tenants()
            .filter(tenant_id=tenant_id, is_active=True)
        )

        for router in routers:
            result = tethering.sweep_router(router, now=now)
            if result is None:
                # Unreachable, or the tables could not be read. Not the same
                # as "nothing to report", so nothing is concluded.
                continue
            swept += 1
            seen += result["seen"]
            acted += result["acted"]

        try:
            closed += tethering.close_stale_cases(tenant_id, now=now)
        except Exception:
            # A throttle left in place is worth an exception in the log and
            # not worth failing this operator's sweep over.
            logger.exception(
                "[tether] could not close stale cases for operator %s",
                tenant_id)

    logger.info(
        "[tether] swept %s router(s): %s sighting(s), %s acted on, %s closed",
        swept, seen, acted, closed)
    return {"routers": swept, "seen": seen, "acted": acted, "closed": closed}


def _lift_blocks(tenant_id):
    """
    Take the reject rules off every router this operator owns.

    The marker is cleared only when every router has been reached and cleaned.
    One unreachable box means it stays set and this runs again next sweep,
    which is the behaviour that matters: the alternative is recording the block
    as lifted while a subscriber is still sitting behind it.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    routers = list(
        RouterDevice.objects.all_tenants()
        .filter(tenant_id=tenant_id, is_active=True)
    )

    lifted = 0
    complete = True
    for router in routers:
        api = safe_connect_router(router)
        if api is None:
            logger.warning(
                "[tether] cannot reach %s to lift its blocks — a subscriber "
                "may still be cut off by a policy that is switched off", router)
            complete = False
            continue
        try:
            lifted += tethering.remove_block_rules(api)
        except Exception:
            logger.exception("[tether] could not lift the blocks on %s", router)
            complete = False

    if complete:
        tethering.set_block_marker(tenant_id, False)
    if lifted:
        logger.info("[tether] lifted %s block rule(s) for operator %s",
                    lifted, tenant_id)
    return lifted


# Long enough to show an operator a pattern — the same subscriber three
# Saturdays running is a different conversation from a one-off — and short
# enough that a table of accusations does not become permanent.
CASE_RETENTION_DAYS = 90


@shared_task
def prune_tethering_cases(days=CASE_RETENTION_DAYS):
    """Drop closed cases past the retention window. Open ones are never touched."""
    from billing.models import TetheringCase

    cutoff = timezone.now() - timezone.timedelta(days=days)
    with all_tenants():
        deleted, _ = (
            TetheringCase.objects.all_tenants()
            .filter(status=TetheringCase.CLEARED, cleared_at__lt=cutoff)
            .delete()
        )
    logger.info("[tether] pruned %s closed case(s) older than %s days",
                deleted, days)
    return deleted
