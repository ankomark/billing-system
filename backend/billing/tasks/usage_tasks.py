import logging
import os

from celery import shared_task
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import (
    Customer,
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
from billing.services.usage import roll_up_day, usage_since
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

    logger.info(f"[usage] Hotspot snapshots collected: {processed}")
    return processed


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def enforce_usage_caps(self):
    """
    Disable access for customers who have exceeded their monthly data cap.

    Ported from the former billing/tasks.py, which was unreachable: the
    billing/tasks package shadowed that module, so nothing could import it.
    Deliberately NOT in CELERY_BEAT_SCHEDULE — enabling automatic cut-off is a
    policy decision. Add a beat entry to switch it on.
    """
    month_start = timezone.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    capped = 0

    customers = (
        Customer.objects.all_tenants()
        .select_related("router", "tenant")
        .filter(status="active")
    )

    for customer in customers:
        subscription = customer.subscriptions.filter(status="active").first()
        if not subscription:
            continue

        cap_gb = customer.custom_data_cap_gb or subscription.package.monthly_data_cap_gb
        if not cap_gb:
            continue  # 0 / None = unlimited

        # The same reader the subscriber's own screen uses. Two sums of one
        # number drift, and the way that surfaces is a customer disconnected
        # while the portal tells them they have data left.
        total = usage_since(customer, month_start)

        used_gb = total / (1024 ** 3)
        if used_gb < cap_gb:
            continue

        # Act as the owning operator. notify_customer() resolves SMS and
        # WhatsApp credentials through get_setting(), which without a tenant in
        # context would pick an arbitrary operator's — sending this customer's
        # message through someone else's account.
        with tenant_context(customer.tenant_id):
            disable_customer_access(customer)
            capped += 1

            try:
                notify_customer(
                    customer.phone,
                    f"Data limit reached ({cap_gb}GB). Please renew or upgrade.",
                )
            except Exception:
                # Notification failure must not undo the enforcement action
                logger.exception(f"[usage-caps] Notify failed for customer {customer.id}")

    logger.info(f"[usage-caps] Capped {capped} customers over their limit")
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
