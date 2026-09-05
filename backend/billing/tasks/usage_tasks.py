import logging
import os

from celery import shared_task
from django.db import transaction
from django.db.models import Prefetch, Sum
from django.utils import timezone

from billing.models import (
    Customer,
    Subscription,
    HotspotUsageRecord,
    HotspotUsageState,
    PPPoEUsageState,
    PPPoEUsageRecord,
)
from billing.notifications import notify_customer
from billing.router_service import (
    disable_customer_access,
    get_hotspot_sessions,
    get_pppoe_sessions,
    tenant_sessions,
)
from billing.services.usage import (
    cap_bytes_for,
    roll_up_day,
    usage_since,
    window_start,
)
from billing.tenancy import all_tenants, tenant_context

logger = logging.getLogger(__name__)


# How long a dispatched per-operator collection stays worth running.
#
# Matches the expires on the beat entries that fan these out. A collection
# reads counters and stores the delta since the last read, so one that has sat
# in the queue longer than the gap between runs would attribute several
# intervals of traffic to a single period and then leave the next run with
# nothing to measure. Dropping it loses one interval; running it late corrupts
# two.
COLLECT_EXPIRES_SECONDS = 240


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def collect_pppoe_usage_snapshots(self):
    """
    Dispatch one PPPoE collection per operator, to run in parallel.

    Reading each router's session table once per operator instead of once per
    subscriber fixed the inner loop, but left the outer one serial: every
    operator on the platform had to be polled in turn, inside the five minutes
    between runs. That is the same shape the per-subscriber version had, one
    level up, and it fails the same way — the beat entry carries expires=240,
    so a run that overruns is dropped rather than delayed and collection stops
    without saying so.

    The cost per operator is a network wait against hardware behind CGNAT, not
    work, so operators are the axis to parallelise. Fanned out, the sweep takes
    as long as the slowest operator rather than the sum of all of them, and one
    operator whose routers have gone dark no longer spends everybody else's
    budget on timeouts.
    """
    tenant_ids = list(
        Customer.objects.all_tenants()
        .filter(
            status="active",
            connection_type="pppoe",
            pppoe_username__isnull=False,
        )
        .values_list("tenant_id", flat=True)
        .distinct()
    )

    for tenant_id in tenant_ids:
        collect_pppoe_usage_for_tenant.apply_async(
            (tenant_id,), expires=COLLECT_EXPIRES_SECONDS)

    logger.info("[usage] PPPoE collection dispatched for %s operator(s)",
                len(tenant_ids))
    return len(tenant_ids)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def collect_pppoe_usage_for_tenant(self, tenant_id):
    """
    Poll one operator's routers and store PPPoE usage deltas.
    Safe for reconnects & counter resets.
    """

    now = timezone.now()
    processed = 0

    customers = (
        Customer.objects.all_tenants()
        .select_related("router", "tenant")
        .filter(
            tenant_id=tenant_id,
            status="active",
            connection_type="pppoe",
            pppoe_username__isnull=False,
        )
    )

    # The subscription each cap is measured against, fetched alongside the
    # customers rather than one query per subscriber inside the loop. At ten
    # thousand subscribers on a five-minute collection that is the difference
    # between two queries and ten thousand, every five minutes, for a check
    # that answers "no cap" for most of them.
    customers = customers.prefetch_related(
        Prefetch(
            "subscriptions",
            queryset=Subscription.objects.all_tenants()
            .filter(status="active", invoice__payment_status="paid")
            .select_related("package")
            .order_by("-expiry_date"),
            to_attr="active_subs",
        )
    )

    # One read of each router's session table for this operator, rather than
    # one connection per subscriber. See _sessions_by_user() for why: the old
    # shape could not finish inside the five minutes between runs once an
    # operator had a few hundred subscribers, and a task that does not finish
    # in time is dropped, not delayed — so collection stopped without saying so.
    try:
        sessions = tenant_sessions(tenant_id, get_pppoe_sessions)
    except Exception as e:
        logger.warning(f"[usage] Router error for operator {tenant_id}: {e}")
        return 0

    for customer in customers:
        router, usage = sessions.get(
            customer.pppoe_username, (None, None))

        if not usage or not usage.get("connected"):
            continue

        # Ownership stated at the write site: a worker has no tenant context,
        # so default_tenant() would refuse once several operators exist.
        state, _ = PPPoEUsageState.objects.get_or_create(
            customer=customer, defaults={"tenant_id": customer.tenant_id}
        )

        # Named for the router's point of view, which is the opposite of the
        # subscriber's — see the note above download_bytes below.
        rx = int(usage.get("rx_bytes", 0))
        tx = int(usage.get("tx_bytes", 0))

        # 🔄 Handle router reboot / counter reset
        if rx < state.last_rx_bytes or tx < state.last_tx_bytes:
            state.last_rx_bytes = rx
            state.last_tx_bytes = tx
            state.last_seen_at = now
            state.save(update_fields=["last_rx_bytes", "last_tx_bytes", "last_seen_at"])
            continue

        rx_delta = rx - state.last_rx_bytes
        tx_delta = tx - state.last_tx_bytes

        if rx_delta < 0 or tx_delta < 0:
            continue

        PPPoEUsageRecord.objects.create(
            tenant_id=customer.tenant_id,
            customer=customer,
            router=router,
            period_start=state.last_seen_at or now,
            period_end=now,
            # Crossed over, because the counters are the router's and the
            # columns are the subscriber's. What the router *received* is what
            # the subscriber sent, so rx is their upload and tx their download.
            # These two were the wrong way round from the first commit until
            # 0064: production had 718GB of "upload" against 63GB of
            # "download", eleven times more sent than received by every
            # subscriber on the platform at once.
            download_bytes=tx_delta,
            upload_bytes=rx_delta,
        )

        state.last_rx_bytes = rx
        state.last_tx_bytes = tx
        state.last_seen_at = now
        state.save(update_fields=["last_rx_bytes", "last_tx_bytes", "last_seen_at"])

        processed += 1

        # Checked here, against the delta that was just written, because this
        # is the earliest moment the system can possibly know the allowance is
        # spent. A separate sweep on its own schedule adds its own interval to
        # the overshoot, and on a 300 MB bundle an interval is a large
        # fraction of the whole bundle.
        #
        # Guarded, and only this subscriber is lost if it raises: a cap check
        # that fails must not abandon the rest of the operator's collection,
        # because the deltas already written are what every later check reads.
        try:
            check_cap(customer, next(iter(customer.active_subs), None))
        except Exception:
            logger.exception(
                "[usage] cap check failed for customer %s", customer.pk)

    logger.info(f"[usage] PPPoE snapshots collected: {processed}")
    return processed


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def collect_hotspot_usage_snapshots(self):
    """
    Poll routers and store hotspot usage deltas.

    Mirrors collect_pppoe_usage_snapshots. Ported from the former
    billing/tasks_usage_hotspot.py, which wrote rows without a tenant and so
    would have failed once a second operator existed.

    Scheduled at collect-hotspot-usage, offset two minutes from its PPPoE twin
    so the two do not open connections to the same routers at once.

    It went unscheduled for a long time, which meant nothing had ever recorded
    a hotspot byte: every hotspot data cap compared against zero, and the usage
    figure a subscriber is shown on the connected page was empty for everybody.
    Written, tested, and never switched on.

    Fanned out per operator, for the reason given on its PPPoE twin. This one
    benefits more: hotspot operators carry far more subscribers each, so a
    serial sweep here ran out of its five minutes sooner.
    """
    tenant_ids = list(
        Customer.objects.all_tenants()
        .filter(status="active", connection_type="hotspot")
        .exclude(hotspot_username="")
        .values_list("tenant_id", flat=True)
        .distinct()
    )

    for tenant_id in tenant_ids:
        collect_hotspot_usage_for_tenant.apply_async(
            (tenant_id,), expires=COLLECT_EXPIRES_SECONDS)

    logger.info("[usage] Hotspot collection dispatched for %s operator(s)",
                len(tenant_ids))
    return len(tenant_ids)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def collect_hotspot_usage_for_tenant(self, tenant_id):
    """
    Poll one operator's routers and store hotspot usage deltas.
    """
    now = timezone.now()
    processed = 0

    customers = (
        Customer.objects.all_tenants()
        .select_related("router", "tenant")
        .filter(tenant_id=tenant_id, status="active", connection_type="hotspot")
        .exclude(hotspot_username="")
    )

    # The subscription each cap is measured against, fetched alongside the
    # customers rather than one query per subscriber inside the loop. At ten
    # thousand subscribers on a five-minute collection that is the difference
    # between two queries and ten thousand, every five minutes, for a check
    # that answers "no cap" for most of them.
    customers = customers.prefetch_related(
        Prefetch(
            "subscriptions",
            queryset=Subscription.objects.all_tenants()
            .filter(status="active", invoice__payment_status="paid")
            .select_related("package")
            .order_by("-expiry_date"),
            to_attr="active_subs",
        )
    )

    # Read each router's table once for this operator, as above. This one
    # matters more: a hotspot operator has far more subscribers than a PPPoE
    # one, and they are the whole product.
    try:
        sessions = tenant_sessions(tenant_id, get_hotspot_sessions)
    except Exception as e:
        logger.warning(
            f"[usage] Hotspot router error for operator {tenant_id}: {e}")
        return 0

    for customer in customers:
        router, usage = sessions.get(
            customer.hotspot_username, (None, None))

        if not usage or not usage.get("connected"):
            continue

        state, _ = HotspotUsageState.objects.get_or_create(
            customer=customer, defaults={"tenant_id": customer.tenant_id}
        )

        # The router's point of view, as in the PPPoE collector above.
        rx = int(usage.get("rx_bytes", 0))
        tx = int(usage.get("tx_bytes", 0))

        # Router reboot or reconnect resets the counters — re-baseline rather
        # than recording a negative delta.
        if rx < state.last_rx_bytes or tx < state.last_tx_bytes:
            state.last_rx_bytes = rx
            state.last_tx_bytes = tx
            state.last_seen_at = now
            state.save(update_fields=["last_rx_bytes", "last_tx_bytes", "last_seen_at"])
            continue

        HotspotUsageRecord.objects.create(
            tenant_id=customer.tenant_id,
            customer=customer,
            router=router,
            period_start=state.last_seen_at or now,
            period_end=now,
            # Crossed over — see the PPPoE collector above for why. bytes-in on
            # /ip/hotspot/active is what the router received from the phone,
            # which is the phone's upload.
            download_bytes=tx - state.last_tx_bytes,
            upload_bytes=rx - state.last_rx_bytes,
        )

        state.last_rx_bytes = rx
        state.last_tx_bytes = tx
        state.last_seen_at = now
        state.save(update_fields=["last_rx_bytes", "last_tx_bytes", "last_seen_at"])
        processed += 1

        # Checked here, against the delta that was just written, because this
        # is the earliest moment the system can possibly know the allowance is
        # spent. A separate sweep on its own schedule adds its own interval to
        # the overshoot, and on a 300 MB bundle an interval is a large
        # fraction of the whole bundle.
        #
        # Guarded, and only this subscriber is lost if it raises: a cap check
        # that fails must not abandon the rest of the operator's collection,
        # because the deltas already written are what every later check reads.
        try:
            check_cap(customer, next(iter(customer.active_subs), None))
        except Exception:
            logger.exception(
                "[usage] cap check failed for customer %s", customer.pk)

    logger.info(f"[usage] Hotspot snapshots collected: {processed}")
    return processed


def _human_bytes(n):
    """Bytes as the operator priced them — 314572800 means nothing to anyone."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f}".rstrip("0").rstrip(".") + "GB"
    return f"{n / 1024 ** 2:.0f}MB"


def cut_off_for_cap(customer, subscription, used, cap):
    """
    Take one subscriber off the network because their data allowance is spent.

    Returns True if this call is the one that cut them off, False if they were
    already cut off — so a caller can count real actions rather than sweeps.

    The hard part of a data cap is not disconnecting somebody once. It is that
    four other things in this system exist to put subscribers *back* on the
    hardware when they fall off it — auto-failover, the router-health recovery
    sweep, the provisioning retry, a tenant being re-enabled — and to all of
    them a disconnected subscriber holding an active paid subscription looks
    exactly like a fault to repair. Disconnecting alone therefore buys minutes:
    the next sweep hands the allowance back and the subscriber carries on.

    So the subscription is suspended first, and only then is the router
    touched. Every one of those paths grants against an *active* subscription
    — see enable_customer_access — so once it is suspended there is nothing
    for them to find. The order is the point: suspending after the disconnect
    leaves a window in which a concurrent sweep sees an active subscription
    and a disconnected subscriber, and puts them back on.

    Idempotent under concurrency. The row is re-read and re-checked under
    select_for_update, so two collectors landing on the same subscriber at
    once produce one suspension, one message and one log line.
    """
    with transaction.atomic():
        locked = (
            Subscription.objects.all_tenants()
            .select_for_update()
            .filter(pk=subscription.pk)
            .first()
        )
        if locked is None or locked.status != "active":
            return False

        locked.status = "suspended"
        locked.capped_at = timezone.now()
        locked.save(update_fields=["status", "capped_at"])

        # Is anything else still keeping this subscriber online?
        #
        # Evaluated after the row above is suspended so it cannot count
        # itself, and phrased the same way enforce_subscription_expiry
        # phrases it — a top-up bought while a bundle was still running is
        # a second live subscription, and cutting the customer row over one
        # allowance running out would take the time they had just paid for
        # with it.
        #
        # Without this the customer row stayed "active" forever after a cap
        # cut-off: an operator's customer list showed somebody as connected
        # who had been off for days, and the expiry sweep would never correct
        # it because that only looks at subscriptions which are still active.
        # Paid, for the same reason _billable_subscription is paid: an
        # abandoned purchase is created active and unpaid, and nothing will
        # ever provision against it. Counting one as coverage would leave a
        # subscriber connected on the strength of a package nobody bought.
        still_covered = (
            Subscription.objects.all_tenants()
            .filter(customer_id=customer.pk, status="active",
                    invoice__payment_status="paid",
                    expiry_date__gt=timezone.now())
            .exists()
        )
        if not still_covered and customer.status != "expired":
            customer.status = "expired"
            customer.save(update_fields=["status"])

    if still_covered:
        # Another live subscription is still serving them, so the network
        # stays on and only this allowance is closed out.
        logger.info(
            "[usage-caps] customer %s spent the allowance on subscription %s "
            "but holds another live subscription; left connected",
            customer.pk, subscription.pk,
        )
        return True

    # Outside the transaction: this talks to hardware that may be a satellite
    # hop away, and holding a row lock open across it would block every other
    # write touching this subscriber for the length of a timeout.
    #
    # Acting as the owning operator, because notify_customer resolves SMS and
    # WhatsApp credentials through get_setting() — with no tenant in context
    # it picks an arbitrary operator's, and sends this subscriber's message
    # through somebody else's account and at their expense.
    with tenant_context(customer.tenant_id):
        try:
            disable_customer_access(customer)
        except Exception:
            # Logged, not re-raised, and the suspension above stands.
            #
            # An unreachable router is the one case where the cut-off cannot
            # be completed now, and the wrong response is to unwind it: the
            # allowance really is spent, and leaving the subscription active
            # so that a later sweep can re-grant it is how a cap turns into a
            # suggestion. Suspended-but-still-connected resolves itself — the
            # session ends on its own, and nothing will provision them again
            # while the subscription is suspended.
            logger.exception(
                "[usage-caps] customer %s suspended over cap, but the router "
                "could not be reached to disconnect them", customer.pk,
            )

        try:
            notify_customer(
                customer.phone,
                f"Your {_human_bytes(cap)} data bundle is used up "
                f"({_human_bytes(used)}). Buy a new bundle to get back online.",
            )
        except Exception:
            # A failed message must never undo an enforcement action.
            logger.exception(
                "[usage-caps] notify failed for customer %s", customer.pk)

    logger.info(
        "[usage-caps] customer %s cut off: %s of %s used",
        customer.pk, _human_bytes(used), _human_bytes(cap),
    )
    return True


def _billable_subscription(customer):
    """
    The subscription a subscriber's allowance is measured against.

    Paid, not merely active, and picked exactly the way enable_customer_access
    picks the one it provisions — because the cap has to be the cap of the
    package they are actually being served on.

    Every subscription is born `active` with an unpaid invoice, so "active"
    says nothing about whether money arrived. Taking the longest-running
    active subscription instead hands the cap to whichever abandoned purchase
    happened to be for the biggest package: somebody who paid for a 300 MB
    bundle and also has an unpaid 10 GB weekly sitting in their history gets
    measured against 10 GB, and never hits their cap at all. This codebase has
    already been bitten by that ordering once — see the note on
    enable_customer_access, and the 25 unpaid-but-active subscriptions found
    in production on 2026-08-25.
    """
    return (
        customer.subscriptions.filter(
            status="active", invoice__payment_status="paid")
        .select_related("package")
        .order_by("-expiry_date")
        .first()
    )


def check_cap(customer, subscription=None):
    """
    Has this subscriber spent their allowance, and if so, cut them off.

    Called from inside the collectors immediately after a delta is written,
    which is what makes the cap actually bite. The sweep below is a safety net
    that runs on a schedule; this runs the instant the traffic is recorded, so
    the overshoot on a 300 MB bundle is bounded by how much can be pulled
    between two collections rather than by two unrelated schedules drifting.

    Returns True if this call cut the subscriber off.
    """
    if subscription is None:
        subscription = _billable_subscription(customer)
    if subscription is None:
        return False

    cap = cap_bytes_for(customer, subscription)
    if not cap:
        return False  # 0 = unlimited

    since = window_start(subscription)
    if since is None:
        return False

    # The same reader the subscriber's own screen uses. Two sums of one number
    # drift, and the way that surfaces is somebody disconnected while the
    # portal tells them they still have data left.
    used = usage_since(customer, since)
    if used < cap:
        return False

    return cut_off_for_cap(customer, subscription, used, cap)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def enforce_usage_caps(self):
    """
    Sweep every active subscriber and cut off any who are over their cap.

    This existed, was correct enough, and did nothing whatsoever: it was never
    added to CELERY_BEAT_SCHEDULE, with a comment explaining that automatic
    cut-off was a policy decision left switched off. Together with a cap field
    that only accepted whole gigabytes, the net effect was that a data cap was
    a number on a form — no subscriber has ever been cut off for exceeding
    one. It is scheduled now.

    Two things enforce the cap, deliberately. check_cap() runs inline in the
    collectors and catches the overwhelming majority the moment the traffic is
    recorded; this sweep catches what that cannot see — a subscriber whose
    router was unreachable during collection, usage that arrived through a
    rollup, and anything that lands between two collections.
    """
    capped = 0

    # Only subscriptions that could still be cut off, and both halves matter.
    # Scanning customers instead re-examined every suspended subscription on
    # every sweep, which on an estate where caps actually bite is most of the
    # table.
    subscriptions = (
        Subscription.objects.all_tenants()
        .select_related("customer", "customer__router", "package", "tenant")
        .filter(status="active", customer__status="active",
                invoice__payment_status="paid")
        .order_by("customer_id", "-expiry_date")
    )

    seen = set()
    for subscription in subscriptions.iterator(chunk_size=200):
        # One subscription per customer — the longest-lived *paid* active one,
        # which is what enable_customer_access provisions against. Also
        # checking a top-up's shorter window would cut somebody off against an
        # allowance they are not being served on, and including unpaid ones
        # would measure them against a package nobody paid for.
        if subscription.customer_id in seen:
            continue
        seen.add(subscription.customer_id)

        try:
            if check_cap(subscription.customer, subscription):
                capped += 1
        except Exception:
            # One subscriber's unreachable router must not end the sweep and
            # leave everybody after them in the ordering uncapped.
            logger.exception(
                "[usage-caps] check failed for customer %s",
                subscription.customer_id,
            )

    logger.info("[usage-caps] cut off %s subscriber(s) over their cap", capped)
    return capped


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def roll_up_usage_daily(self, days_back=2):
    """
    Fold finished days of five-minute deltas into one row per subscriber.

    One raw row per subscriber per five minutes is 2.88 million a day at ten
    thousand subscribers, and a billion a year. The same information as a daily
    total is ten thousand rows a day.

    Re-rolls the last couple of days rather than only yesterday: a collector
    that was retrying, or a router that came back late, can add rows to a day
    after midnight has passed, and roll_up_day recomputes rather than adds so
    doing it twice costs nothing but time.

    Nothing prunes the raw rows yet. That comes once these two have agreed on
    live data for a while — the saving is in the prune, the correctness is in
    getting here first, and deleting the only copy before the replacement has
    been watched is how a data loss happens.
    """
    today = timezone.localdate(timezone.now())
    written = 0
    for back in range(1, max(1, days_back) + 1):
        written += roll_up_day(today - timezone.timedelta(days=back))

    logger.info(f"[usage] Daily rollup wrote {written} rows")
    return written


# How long raw five-minute deltas are kept after a day has been rolled up.
#
# Ninety days is deliberately generous, and doubles as the "watch it for a
# while" period the rollup was written to wait for: nothing is deleted until
# the rollups have been agreeing with the raw rows for a full quarter, and by
# then a disagreement has had every chance to surface on the portal or in a cap.
#
# The floor is set by usage_since(), not by disk. It reads whole days from the
# rollup but always reads the *part-day at the start of a window* from the raw
# rows — a subscription bought at 14:30 cannot take that day from a daily total
# without charging somebody for traffic from before they paid. So raw rows must
# outlive the longest window anybody asks about:
#
#   enforce_usage_caps   → month_start, which is exactly midnight, so the whole
#                          window comes from rollups. Unaffected by any value.
#   views.py:2388        → subscription.start_date, which is not. A monthly
#                          package sits comfortably inside 90 days; a yearly one
#                          does not, and loses its first part-day once pruned.
#
# That loss is bounded at one day of one subscriber's traffic, and it
# under-counts rather than over-counts — the subscriber gets marginally more
# than their cap rather than being cut off early, which is the right direction
# to fail in. Lower this to 45 once the rollups have been trusted for a while;
# do not go below the longest package duration sold without accepting that
# trade for its first day.
USAGE_RAW_RETENTION_DAYS = int(os.getenv("USAGE_RAW_RETENTION_DAYS", "90"))

# Rows per DELETE. At ten thousand subscribers a single day holds 2.88 million
# raw rows, and deleting a day in one statement means one transaction holding
# locks over all of them while autovacuum reclaims nothing behind it. Chunking
# keeps each transaction short enough that the collectors writing to the same
# table every five minutes are not waiting on it.
USAGE_PRUNE_CHUNK = 10_000


def _day_bounds(day):
    """The aware datetimes a local calendar day spans, as roll_up_day cuts it."""
    start = timezone.make_aware(
        timezone.datetime.combine(day, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )
    return start, start + timezone.timedelta(days=1)


@shared_task
def prune_usage_records(days=None):
    """
    Drop raw five-minute deltas for days the rollup has already covered.

    This is the half of the rollup that was never switched on. Collection
    writes one row per active subscriber per five minutes and nothing ever
    removed one: 2.88 million rows a day at ten thousand subscribers, a billion
    a year, on the same disk as the database itself. A full disk on this box is
    not a slow platform, it is a stopped one.

    **Only deletes a day that has been rolled up.** A day with no UsageRecord
    rows is skipped and logged, never deleted — if the rollup has been failing,
    the raw rows are the only copy of that traffic, and throwing them away is
    how a month of billing data disappears without anybody noticing. The check
    is per day rather than per subscriber, which is coarse: it proves the
    rollup ran that day, not that it caught every subscriber. That is the right
    trade, because roll_up_day() recomputes a day from the raw rows rather than
    appending to it, so a day it has run over is a day it has fully covered.
    """
    from billing.models import HotspotUsageRecord, PPPoEUsageRecord, UsageRecord

    days = USAGE_RAW_RETENTION_DAYS if days is None else days
    cutoff_day = timezone.localdate(timezone.now()) - timezone.timedelta(days=days)
    cutoff_start, _ = _day_bounds(cutoff_day)

    deleted_total = 0
    skipped = 0

    # Every block below pairs .all_tenants() with the context manager of the
    # same name, inside a transaction. The manager method lifts this app's ORM
    # filter; only the context manager clears the row-level security that
    # Postgres applies underneath it, and only inside a transaction, because it
    # clears the setting with set_config(..., local=true). Without both, this
    # walks one operator's rows while reading as though it walked everybody's —
    # and the failure mode of a *prune* that sees a subset is not lost data but
    # a table that never shrinks and no error to say why.
    for model, kind in ((PPPoEUsageRecord, "pppoe"), (HotspotUsageRecord, "hotspot")):
        # Distinct days, resolved in SQL rather than by reading every row's
        # timestamp back into Python — at this table's size that difference is
        # the whole cost of the task.
        with transaction.atomic(), all_tenants():
            stale_days = list(
                model.objects.all_tenants()
                .filter(period_start__lt=cutoff_start)
                .dates("period_start", "day")
            )

        for day in stale_days:
            with transaction.atomic(), all_tenants():
                rolled = UsageRecord.objects.all_tenants().filter(
                    date=day, connection_type=kind).exists()

            if not rolled:
                # No rollup for this day: the raw rows are the only record of
                # it. Leave them and say so — a run of these means the rollup
                # is broken and wants looking at, not that the prune is stuck.
                skipped += 1
                logger.warning(
                    "[usage-prune] %s %s has no rollup — keeping its raw rows",
                    kind, day)
                continue

            start, end = _day_bounds(day)
            while True:
                # One short transaction per chunk rather than one long one per
                # day. A day is millions of rows at scale, and holding them all
                # in a single transaction blocks the collectors writing to this
                # same table every five minutes and stops autovacuum reclaiming
                # anything behind it.
                with transaction.atomic(), all_tenants():
                    ids = list(
                        model.objects.all_tenants()
                        .filter(period_start__gte=start, period_start__lt=end)
                        .values_list("id", flat=True)[:USAGE_PRUNE_CHUNK]
                    )
                    if ids:
                        model.objects.all_tenants().filter(id__in=ids).delete()
                if not ids:
                    break
                deleted_total += len(ids)

    logger.info(
        "[usage-prune] deleted %s raw usage row(s) older than %s days%s",
        deleted_total, days,
        f", skipped {skipped} unrolled day(s)" if skipped else "")
    return deleted_total
