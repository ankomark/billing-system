from celery import shared_task
from django.utils import timezone
import logging

from billing.models import (
    Customer,
    PPPoEUsageState,
    PPPoEUsageRecord,
)
from billing.router_service import get_pppoe_live_usage_any_router

logger = logging.getLogger(__name__)


@shared_task
def collect_pppoe_usage_snapshots():
    """
    Poll routers and store PPPoE usage deltas.
    """

    now = timezone.now()
    customers = Customer.objects.filter(
        status="active",
        connection_type="pppoe",
        pppoe_username__isnull=False,
    )

    processed = 0

    for customer in customers:
        router, usage = get_pppoe_live_usage_any_router(customer)
        if not usage or not usage.get("connected"):
            continue

        state, _ = PPPoEUsageState.objects.get_or_create(customer=customer)

        rx = usage["rx_bytes"]
        tx = usage["tx_bytes"]

        # Handle router reset / counter rollover
        if rx < state.last_rx_bytes or tx < state.last_tx_bytes:
            state.last_rx_bytes = rx
            state.last_tx_bytes = tx
            state.last_seen_at = now
            state.save()
            continue

        rx_delta = rx - state.last_rx_bytes
        tx_delta = tx - state.last_tx_bytes

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
        state.save()

        processed += 1

    logger.info(f"[usage] PPPoE snapshots collected: {processed}")
    return processed
