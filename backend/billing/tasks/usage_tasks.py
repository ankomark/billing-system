import logging
from celery import shared_task
from django.utils import timezone

from billing.models import (
    Customer,
    PPPoEUsageState,
    PPPoEUsageRecord,
)
from billing.router_service import get_pppoe_live_usage_any_router

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def collect_pppoe_usage_snapshots(self):
    """
    Poll routers and store PPPoE usage deltas.
    Safe for reconnects & counter resets.
    """

    now = timezone.now()
    processed = 0

    customers = (
        Customer.objects
        .select_related("router")
        .filter(
            status="active",
            connection_type="pppoe",
            pppoe_username__isnull=False,
        )
    )

    for customer in customers:
        try:
            router, usage = get_pppoe_live_usage_any_router(customer)
        except Exception as e:
            logger.warning(
                f"[usage] Router error for customer {customer.id}: {e}"
            )
            continue

        if not usage or not usage.get("connected"):
            continue

        state, _ = PPPoEUsageState.objects.get_or_create(customer=customer)

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
            customer=customer,
            router=router,
            period_start=state.last_seen_at or now,
            period_end=now,
            download_bytes=rx_delta,
            upload_bytes=tx_delta,
        )

        state.last_rx_bytes = rx
        state.last_tx_bytes = tx
        state.last_seen_at = now
        state.save(update_fields=["last_rx_bytes", "last_tx_bytes", "last_seen_at"])

        processed += 1

    logger.info(f"[usage] PPPoE snapshots collected: {processed}")
    return processed
