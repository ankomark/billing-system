from celery import shared_task
from django.conf import settings
import logging
from django.utils import timezone

from billing.models import Customer
from billing.tenancy import tenant_context
from billing.router_service import (
    safe_connect_router,
    disconnect_pppoe_session,
    enable_customer_access,
    disable_customer_access,
    RouterUnreachable,
)

logger = logging.getLogger(__name__)


# =====================================================
# INTERNAL HELPERS
# =====================================================

def _mark_router_online(router):
    # Through record_health so the transition is logged. These used to write
    # is_online directly, which is why a router could go down and come back with
    # nothing recording that it had.
    router.record_health(True)


def _mark_router_offline(router, error):
    router.record_health(False, error=error)


def _load_customer(customer_id):
    """
    Load by primary key without tenant scoping.

    A worker has no request, so nothing has set the tenant context yet — the
    row itself is what tells us which operator we are acting for. The caller
    then enters that operator's context before touching routers or credentials.
    """
    return (
        Customer.objects.all_tenants()
        .select_related("router", "tenant")
        .get(id=customer_id)
    )


# =====================================================
# PPPoE CONTROL TASKS
# =====================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def disconnect_pppoe_task(self, customer_id):
    """
    Safely disconnect a PPPoE customer.
    Used by:
    - Admin disconnect
    - Customer self-service
    - Suspension workflows
    """
    customer = _load_customer(customer_id)

    if customer.connection_type != "pppoe":
        logger.info(f"[disconnect_pppoe_task] Customer {customer_id} not PPPoE")
        return False

    if not customer.router:
        logger.warning(f"[disconnect_pppoe_task] No router assigned to {customer_id}")
        return False

    router = customer.router

    with tenant_context(customer.tenant_id):
        try:
            api = safe_connect_router(router)
            if not api:
                raise ConnectionError("Router unreachable")

            disconnect_pppoe_session(api, customer.pppoe_username)
            _mark_router_online(router)

            logger.info(
                f"[disconnect_pppoe_task] PPPoE disconnected for customer {customer_id}"
            )
            return True

        except Exception as e:
            _mark_router_offline(router, e)
            logger.error(f"[disconnect_pppoe_task] Failed for {customer_id}: {e}")
            raise


# =====================================================
# CUSTOMER ACCESS CONTROL TASKS
# =====================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=15,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def enable_customer_task(self, customer_id):
    """
    Enable internet access for a customer (PPPoE or Hotspot).
    Used after:
    - Successful payment
    - Resume
    - Renewal
    """
    customer = _load_customer(customer_id)

    if not customer.router:
        logger.warning(f"[enable_customer_task] No router for customer {customer_id}")
        return False

    router = customer.router

    with tenant_context(customer.tenant_id):
        try:
            enable_customer_access(customer)
            _mark_router_online(router)

            logger.info(f"[enable_customer_task] Access enabled for customer {customer_id}")
            return True

        except Exception as e:
            _mark_router_offline(router, e)
            logger.error(f"[enable_customer_task] Failed for {customer_id}: {e}")
            raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=15,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def disable_customer_task(self, customer_id):
    """
    Disable internet access for a customer.
    Used for:
    - Suspension
    - Expiry
    - Admin action
    """
    customer = _load_customer(customer_id)

    if not customer.router:
        logger.warning(f"[disable_customer_task] No router for customer {customer_id}")
        return False

    router = customer.router

    with tenant_context(customer.tenant_id):
        try:
            disable_customer_access(customer)
            _mark_router_online(router)

            logger.info(f"[disable_customer_task] Access disabled for customer {customer_id}")
            return True

        except RouterUnreachable as e:
            # Retried, but not counted against the router.
            #
            # Deliberately not _mark_router_offline. Whether a router is up is
            # the health sweep's question, and it answers it every two minutes
            # with a guard that takes three consecutive failures. Letting this
            # vote as well is what broke that guard on 2026-08-31: twenty-six
            # callers failing at once crossed a six-minute threshold in
            # seconds, and auto-failover emptied a router that was never down.
            # An expiry batch during an outage is exactly that shape -- dozens
            # of these at once.
            #
            # Re-raised so autoretry_for picks it up. The subscriber is still
            # online and that is worth another three attempts.
            logger.warning(
                "[disable_customer_task] customer %s is still online: %s",
                customer_id, e)
            raise

        except Exception as e:
            _mark_router_offline(router, e)
            logger.error(f"[disable_customer_task] Failed for {customer_id}: {e}")
            raise


# =====================================================
# DEVICE CONTROL TASKS
# =====================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    # Longer and more patient than the tasks above, because what this is
    # waiting for is different. Those retry a router that is briefly busy;
    # this one retries a router that was *down* when an operator blocked a
    # handset, and a link that drops does not always come back inside a
    # minute. Roughly 30s, 1m, 2m, 4m, 8m then 10m apart — about an hour of
    # trying before it gives up.
    retry_backoff=30,
    retry_backoff_max=600,
    retry_kwargs={"max_retries": 10},
    retry_jitter=True,
)
def kick_device_task(self, customer_id, mac_address, only_if_uncovered=False):
    """
    Keep trying to take one device off the hardware.

    Blocking a device wrote the block immediately and attempted the
    disconnect exactly once. When no router answered that attempt, the
    operator was told so and nothing ever tried again — and an established
    hotspot session does not end on its own until `limit-uptime` runs out,
    which enable_hotspot sets to whatever is left of the subscription. On a
    monthly package that is days of a handset staying online after it was
    blocked, with no sweep anywhere that would notice.

    So the block itself stays synchronous — the operator gets an immediate,
    honest answer about what was reached — and this carries the part that
    needs to outlive the request.

    Deliberately does not touch router health. Marking a router offline from
    here would let an hour of device-kick retries feed the consecutive-failure
    count that auto-failover migrates subscribers on, which is a much bigger
    action than this task is entitled to take. check_router_health_task
    already tracks that, on its own schedule and its own evidence.

    `only_if_uncovered` is for a device whose package ran out, and is asked
    again on every attempt, because an hour of retries is long enough for the
    answer to change. Removal is by MAC, so a phone that buys again on the
    spot, or an address since handed to somebody else, would otherwise have
    the account it was just given taken away by a retry still working through
    the old one.
    """
    from billing.models import CustomerDevice
    from billing.router_service import (
        _tenant_routers, connect_router, disable_hotspot,
    )
    from billing.services.entitlement import subscription_for_device
    from billing.utils import mac_variants

    customer = _load_customer(customer_id)

    with tenant_context(customer.tenant_id):
        if only_if_uncovered and (
            not CustomerDevice.objects.all_tenants()
            .filter(tenant_id=customer.tenant_id, customer=customer,
                    mac_address__in=mac_variants(mac_address))
            .exists()
            or subscription_for_device(customer, mac_address) is not None
        ):
            logger.info(
                "[kick_device_task] %s is covered again or no longer "
                "customer %s's — left alone", mac_address, customer_id)
            return False

        routers = list(_tenant_routers(customer.tenant_id))

        # A router the health sweep has already condemned is not worth an hour
        # of retries. fiber1 has been unreachable since 19 August and answers
        # nothing; every eviction still queued ten attempts against it, each
        # blocking a worker slot on a connect timeout. On 2026-09-07 that ran
        # alongside 152 provisioning retries and emptied a four-slot pool, at
        # which point the health sweep -- which carries expires=90 -- stopped
        # being scheduled at all, is_online went stale, and the dashboard
        # reported two live routers as offline.
        #
        # Nothing is lost by skipping it. A device left behind on a router
        # nobody can reach is exactly what disable_orphan_hotspot_users exists
        # to find, and it says so: "a router that comes back after an outage
        # serves whatever it had when it left, so re-run this once it is up."
        # This defers the cleanup to the thing designed for it instead of
        # spending the pool proving the router is still down.
        #
        # Condemned by evidence, not merely flagged. is_online is False on a
        # router nobody has probed yet -- the column default -- so reading the
        # flag alone would skip a newly added box, and skip every box for the
        # first two minutes after a restart, silently doing nothing. The
        # failure count is positive evidence: record_health increments it on
        # each miss and only condemns at ROUTER_OFFLINE_AFTER_FAILURES, so a
        # never-probed router sits at 0 and is tried, while fiber1 sits in the
        # thousands and is not.
        threshold = settings.ROUTER_OFFLINE_AFTER_FAILURES
        condemned = lambda r: (not r.is_online
                               and r.consecutive_failures >= threshold)
        reachable = [r for r in routers if not condemned(r)]
        skipped = [r.name for r in routers if condemned(r)]
        if skipped:
            logger.info(
                "[kick_device_task] skipping %s for %s — declared offline by "
                "the health sweep; disable_orphan_hotspot_users covers it",
                ", ".join(skipped), mac_address)
        routers = reachable

        if not routers:
            logger.warning(
                "[kick_device_task] No reachable router for customer %s, so "
                "there is nothing holding %s online", customer_id, mac_address)
            return False

        unfinished = []
        for router in routers:
            try:
                api = connect_router(router)
                if disable_hotspot(api, mac_address):
                    logger.info(
                        "[kick_device_task] %s is off %s", mac_address, router)
                else:
                    unfinished.append(str(router))
            except Exception as exc:
                logger.warning(
                    "[kick_device_task] could not reach %s to drop %s: %s",
                    router, mac_address, exc)
                unfinished.append(str(router))

        if unfinished:
            # Raised so autoretry_for sees it. A return would look like
            # success and end the retries with the device still connected.
            raise RuntimeError(
                f"could not confirm {mac_address} is off "
                f"{', '.join(unfinished)}"
            )

        return True


@shared_task(name="billing.tasks.router_tasks.disable_orphan_hotspot_users_task")
def disable_orphan_hotspot_users_task():
    """
    The nightly reconciler behind kick_device_task.

    Eviction now asks every router the operator owns and hands what it cannot
    confirm to kick_device_task, which retries for about an hour. That closes
    the leak found on 2026-09-07 — 44 enabled hotspot accounts belonging to
    nobody, 7.30GB served — at its source, and an hour covers the link flap
    that caused most of it.

    It does not cover an outage longer than an hour. fiber1 has been down since
    19 August; a device evicted against a router in that state is beyond every
    retry this system has. Nor does it cover the next path that learns to leave
    an account behind, which is the failure this whole class keeps repeating:
    the router goes on serving what the database stopped tracking, and nothing
    anywhere disagrees.

    So the sweep stays, for the same reason enforce_usage_caps stays behind the
    inline check and sync_router_profiles stays behind provisioning. A
    reconciler that finds nothing every night is the evidence that the thing in
    front of it is working — not a reason to remove it.

    Disables, never deletes, and refuses a router with more orphans than
    --max-disable rather than acting on a number that means its own database
    query is wrong. See the command for both.
    """
    from django.core.management import call_command
    from io import StringIO

    out = StringIO()
    try:
        call_command("disable_orphan_hotspot_users", fix=True, stdout=out,
                     stderr=out)
    except Exception:
        logger.exception("[orphan-sweep] failed")
        raise

    report = out.getvalue()
    # Logged whole, at info, and not summarised. It is a handful of lines on a
    # normal night and the only record that this ran at all; an operator asked
    # why a device stopped working needs to be able to find the line that says
    # this disabled it.
    logger.info("[orphan-sweep]\n%s", report.strip() or "(nothing to report)")
    return report


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
    retry_jitter=True,
)
def ensure_lease_script_task(self):
    """
    Keep the DHCP lease script present on every hotspot router.

    The script is what makes a hotspot session follow its address instead of
    stranding on the old one, and it lives in router configuration -- so it
    survives a reboot, and does not survive a reset, a restore from an old
    backup, or a router swapped for a spare. Any of those bring the stranding
    back silently, with nothing to say why subscribers at that station are
    connected with no internet.

    Asserting it on a schedule is what makes the fix permanent rather than
    true once. Hourly is plenty: the periodic session sweep covers the gap in
    the meantime, and a router that comes back without the script is a rare
    event measured in months.
    """
    from billing.services.lease_script import ensure_lease_script_everywhere
    from billing.services.walled_garden import (
        close_resolver_bypasses_everywhere,
    )

    was_set, present, skipped = ensure_lease_script_everywhere(apply=True)
    if was_set or skipped:
        logger.info(
            "[lease-script] installed on %s server(s), %s already correct, "
            "%s left alone", was_set, present, skipped)

    # And the other way a subscriber ends up connected with no internet and no
    # page to act on. A walled-garden rule letting an unauthorised device reach
    # a public resolver directly gives it working DNS, so the phone never
    # concludes it is behind a captive portal and never offers the sign-in
    # prompt -- while every real connection is dropped. Both routers were
    # carrying four such rules each.
    #
    # Asserted here rather than once, for the same reason as the lease script:
    # a restore from an old backup brings them straight back, and nothing about
    # the symptom points at the walled garden.
    try:
        closed = close_resolver_bypasses_everywhere(apply=True)
        if closed:
            logger.info(
                "[walled-garden] closed %s resolver bypass rule(s)", closed)
    except Exception:
        # Never let this stop the lease script being reported as done.
        logger.exception("[walled-garden] sweep failed")

    # And the PPPoE side of the same complaint. RouterOS ships a 10-second
    # keepalive, which on a wireless backhaul ends a session over an ordinary
    # blip and leaves the subscriber's router redialling.
    try:
        from billing.services.lease_script import (
            ensure_pppoe_keepalive_everywhere,
        )

        raised = ensure_pppoe_keepalive_everywhere(apply=True)
        if raised:
            logger.info(
                "[pppoe] keepalive corrected on %s server(s)", raised)
    except Exception:
        logger.exception("[pppoe] keepalive sweep failed")

    # PPP secrets nobody holds any more. A PPPoE secret is a working login for
    # unmetered internet, and deleting a customer used to leave theirs on every
    # router -- three were still dialling-ready weeks after being deleted
    # through the customers page. perform_destroy now clears them at the
    # source; this catches whatever does not go through it.
    try:
        from billing.services.pppoe_orphans import (
            sweep_orphan_secrets_everywhere,
        )

        gone, purged = sweep_orphan_secrets_everywhere(apply=True)
        if gone or purged:
            logger.info(
                "[pppoe-orphans] disabled %s secret(s), removed %s",
                gone, purged)
    except Exception:
        logger.exception("[pppoe-orphans] sweep failed")

    # The hotspot equivalent, and the one that needs no credentials at all.
    # The MikroTik setup wizard leaves an `admin` hotspot user behind on the
    # `default` profile -- no uptime limit, no data limit, no rate limit -- so
    # anybody who typed its name and password into the portal was on the
    # network unmetered and for ever, with no purchase anywhere behind them.
    # skylink3 was carrying one. It survives a reboot and returns with any
    # restore, which is why it is asserted here rather than closed once.
    try:
        from billing.services.hotspot_logins import (
            close_unearned_logins_everywhere,
        )

        shut = close_unearned_logins_everywhere(apply=True)
        if shut:
            logger.warning(
                "[hotspot-logins] disabled %s login(s) that needed no "
                "purchase", shut)
    except Exception:
        logger.exception("[hotspot-logins] sweep failed")

    # And the accounts that did have a purchase behind them once. Expiry takes
    # off the devices of the package that ended; a payment landing before its
    # code is typed can leave an account on a handset the code never reached.
    # Both are closed at the source, and this is the reconciler behind them --
    # the same shape as the orphan sweep, for the same reason: the router goes
    # on serving what the database stopped tracking, and nothing else is
    # looking.
    try:
        from billing.services.uncovered_logins import (
            close_uncovered_logins_everywhere,
        )

        off, ended = close_uncovered_logins_everywhere(apply=True)
        if off:
            logger.warning(
                "[uncovered] disabled %s account(s) no live package covers, "
                "ending %s session(s)", off, ended)
    except Exception:
        logger.exception("[uncovered] sweep failed")

    # Everything above starts from the account table, so both of these are
    # invisible to all of it.
    #
    # A session outlives the account that was disabled or removed underneath
    # it -- RouterOS counts down session-time-left once somebody is on, and
    # nothing here was reading it. skylink3 was serving one with 4 days and
    # 16 GB left hours after the sweep that should have ended it.
    try:
        from billing.services.stale_sessions import (
            close_stale_sessions_everywhere,
        )

        ended = close_stale_sessions_everywhere(apply=True)
        if ended:
            logger.warning(
                "[stale-sessions] ended %s session(s) with no account behind "
                "them", ended)
    except Exception:
        logger.exception("[stale-sessions] sweep failed")

    # And a bypassed ip-binding, which needs no account in the first place.
    # Reports rather than closes: bypassing a till or a camera is a reasonable
    # thing to have done on purpose, and those devices have no browser to sign
    # in with. Mark the deliberate ones KEEP and this goes quiet; pass
    # apply=True here once they are marked.
    try:
        from billing.services.hotspot_bindings import (
            close_bypassing_bindings_everywhere,
        )

        open_bindings = close_bypassing_bindings_everywhere(apply=False)
        if open_bindings:
            logger.warning(
                "[bindings] %s binding(s) let a device past the portal with "
                "no account", open_bindings)
    except Exception:
        logger.exception("[bindings] sweep failed")

    return was_set
