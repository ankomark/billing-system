from django.utils import timezone
from django.db import transaction

from billing.models import Customer, HotspotUsageState, HotspotUsageRecord
from billing.router_service import get_hotspot_live_usage_any_router


def collect_hotspot_usage(interval_seconds=300):
    now = timezone.now()
    start = now - timezone.timedelta(seconds=interval_seconds)

    customers = Customer.objects.filter(
        status="active",
        connection_type="hotspot"
    )

    for c in customers:
        router, live = get_hotspot_live_usage_any_router(c)
        if not live or not live.get("connected"):
            continue

        rx = int(live.get("rx_bytes") or 0)
        tx = int(live.get("tx_bytes") or 0)

        with transaction.atomic():
            state, _ = HotspotUsageState.objects.select_for_update().get_or_create(
                customer=c
            )

            delta_rx = max(0, rx - (state.last_rx_bytes or 0))
            delta_tx = max(0, tx - (state.last_tx_bytes or 0))

            HotspotUsageRecord.objects.create(
                customer=c,
                router=router,
                period_start=start,
                period_end=now,
                download_bytes=delta_rx,
                upload_bytes=delta_tx,
            )

            state.last_rx_bytes = rx
            state.last_tx_bytes = tx
            state.last_seen_at = now
            state.save()
