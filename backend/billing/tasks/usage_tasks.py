import logging
from celery import shared_task
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
    get_hotspot_live_usage_any_router,
    get_pppoe_live_usage_any_router,
)
from billing.tenancy import tenant_context

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
        Customer.objects.all_tenants()
        .select_related("router", "tenant")
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

        # Ownership stated at the write site: a worker has no tenant context,
        # so default_tenant() would refuse once several operators exist.
        state, _ = PPPoEUsageState.objects.get_or_create(
            customer=customer, defaults={"tenant_id": customer.tenant_id}
        )

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

    Not in CELERY_BEAT_SCHEDULE — there was no hotspot entry before either, so
    scheduling it would be a behaviour change rather than a fix. Add one
    alongside collect-pppoe-usage to switch it on.
    """
    now = timezone.now()
    processed = 0

    customers = (
        Customer.objects.all_tenants()
        .select_related("router", "tenant")
        .filter(status="active", connection_type="hotspot")
        .exclude(hotspot_username="")
    )

    for customer in customers:
        try:
            router, usage = get_hotspot_live_usage_any_router(customer)
        except Exception as e:
            logger.warning(f"[usage] Hotspot router error for customer {customer.id}: {e}")
            continue

        if not usage or not usage.get("connected"):
            continue

        state, _ = HotspotUsageState.objects.get_or_create(
            customer=customer, defaults={"tenant_id": customer.tenant_id}
        )

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
            download_bytes=rx - state.last_rx_bytes,
            upload_bytes=tx - state.last_tx_bytes,
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

        if customer.connection_type == "pppoe":
            model = PPPoEUsageRecord
        else:
            model = HotspotUsageRecord

        total = model.objects.filter(
            customer=customer,
            period_start__gte=month_start,
        ).aggregate(
            used=Sum("download_bytes") + Sum("upload_bytes")
        )["used"] or 0

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
