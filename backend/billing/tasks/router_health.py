import logging
from celery import shared_task

from billing.tenancy import all_tenants

logger = logging.getLogger(__name__)


# How long a dispatched probe stays worth running.
#
# Matches the expires on the beat entry that fans these out. A probe is a
# statement about *now*, so one that has been queued behind a backlog for
# longer than the gap between sweeps has nothing useful left to say — and
# running it anyway would write a stale is_online that the next sweep has
# already superseded. Dropped is the right outcome, not delayed.
PROBE_EXPIRES_SECONDS = 90


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def check_router_health_task(self):
    """
    Dispatch one health probe per router, to run in parallel.

    This used to do the probing itself, in a single serial loop. Every
    iteration of that loop is a network wait rather than work: a reachable
    router costs a TCP connect plus an API login, and an unreachable one costs
    the full three-second timeout inside is_router_reachable before the next
    router is even tried. Every router on the platform therefore had to fit,
    one after another, into the two minutes between runs.

    At a handful of routers that is invisible. At a hundred — fifty operators
    with two apiece — a fifth of them offline is a minute of pure timeout
    before the reachable ones are reached at all, and the sweep stops fitting.
    What follows is not a late sweep. This task carries expires=90 in
    CELERY_BEAT_SCHEDULE, so while the previous run is still holding a worker
    the next one is *discarded*, and is_online quietly goes stale with nothing
    logged to say so. auto-failover reads that field every three minutes and
    migrates subscribers off routers it believes are down, so stale health is
    not a cosmetic problem — it moves people on the strength of it.

    Fanned out, a sweep takes as long as the slowest single router instead of
    the sum of all of them, and one dead router costs three seconds once rather
    than three seconds of every other router's budget. Adding operators now
    costs queue depth, which the pool absorbs, instead of wall-clock time in a
    window that cannot stretch.
    """
    from billing.models import RouterDevice

    # Health probing is legitimately platform-wide: every operator's
    # routers must be checked, so this opts out of scoping explicitly.
    with all_tenants():
        router_ids = list(
            RouterDevice.objects.all_tenants()
            .filter(is_active=True)
            .values_list("id", flat=True)
        )

    for router_id in router_ids:
        check_single_router_health.apply_async(
            (router_id,), expires=PROBE_EXPIRES_SECONDS)

    logger.info("[router-health] dispatched %s probe(s)", len(router_ids))
    return len(router_ids)


@shared_task
def check_single_router_health(router_id):
    """
    Probe one router and update is_online / last_seen / last_error.

    Deliberately without autoretry. A router that does not answer is not an
    error here — safe_connect_router catches that and records it as health —
    so the only thing a retry could fire on is a bug in this function, and
    retrying that just multiplies it across every router at once. The sweep
    runs again in two minutes regardless, which is a better recovery than a
    retry storm against hardware that is already struggling.
    """
    from billing.models import RouterDevice
    from billing.router_service import safe_connect_router

    with all_tenants():
        router = (
            RouterDevice.objects.all_tenants().filter(id=router_id).first()
        )

    if router is None:
        # Deleted or deactivated between dispatch and pickup. Normal, not worth
        # a warning — the next sweep simply will not dispatch it.
        return None

    # The one caller that votes on health. Everything else may fail against a
    # router without condemning it — see safe_connect_router for why that
    # matters, and what it cost when twenty-six callers all had a vote.
    api = safe_connect_router(router, count_failure=True)
    if api:
        # The old loop never closed these. One leaked connection per reachable
        # router per two minutes is nothing at two routers and is a RouterOS
        # session table full of dead entries at a hundred — MikroTik holds them
        # until its own idle timeout fires, and the box has a finite number.
        try:
            api.close()
        except Exception:
            logger.debug("[router-health] %s: close failed", router.name)
        logger.info(f"[router-health] {router.name} ONLINE")
        return True

    logger.warning(
        f"[router-health] {router.name} OFFLINE — {router.last_error}")
    return False


# Long enough to investigate a pattern of flapping, short enough that the table
# does not grow without bound. Only transitions are stored, so a stable estate
# writes almost nothing and this rarely deletes anything.
EVENT_RETENTION_DAYS = 90


@shared_task
def prune_router_events_task(days=EVENT_RETENTION_DAYS):
    """
    Drop router events past the retention window.

    Every sweep that finds a flapping router adds rows, and nothing else would
    ever remove them.
    """
    from django.utils import timezone
    from billing.models import RouterEvent

    cutoff = timezone.now() - timezone.timedelta(days=days)
    with all_tenants():
        deleted, _ = RouterEvent.objects.all_tenants().filter(
            created_at__lt=cutoff).delete()
    logger.info("[router-health] pruned %s router event(s) older than %s days",
                deleted, days)
    return deleted


ATTEMPT_RETENTION_DAYS = 14


@shared_task
def prune_connection_attempts_task(days=ATTEMPT_RETENTION_DAYS):
    """
    Drop refused-connection records past the retention window.

    A diagnostic, not a ledger: it answers "is anybody struggling to get on
    today", and a code somebody mistyped a month ago is not worth holding. It
    also grows with every failure, and nothing else would remove a row.
    """
    from django.utils import timezone
    from billing.models import ConnectionAttempt

    cutoff = timezone.now() - timezone.timedelta(days=days)
    with all_tenants():
        deleted, _ = ConnectionAttempt.objects.all_tenants().filter(
            created_at__lt=cutoff).delete()
    logger.info("[attempts] pruned %s connection attempt(s) older than %s days",
                deleted, days)
    return deleted
