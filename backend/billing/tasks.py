from django.utils import timezone
from datetime import timedelta
from .models import Customer,UsageRecord
from billing.notifications import notify_customer
from billing.models import Subscription, ExpiryReminderLog,HotspotUsageRecord,Customer, PPPoEUsageRecord,notify_customer
from billing.router_service import disable_customer_access
from billing.notifications import send_sms  # your SMS function
from .router_service import enable_customer_access
from billing.router_service import get_pppoe_live_usage
from django.db.models import Sum
# =====================================================
# 1️⃣ EXPIRE SUBSCRIPTIONS
# =====================================================

def expire_subscriptions():
    """
    - Marks expired subscriptions as 'expired'
    - Updates customer status
    - Removes internet access from MikroTik
    """
    now = timezone.now()

    expired_subs = Subscription.objects.filter(
        status="active",
        expiry_date__lte=now,
    )

    for sub in expired_subs:
        customer = sub.customer

        # Mark subscription as expired
        sub.status = "expired"
        sub.save()

        # If customer has no OTHER active subscription → mark customer expired
        if not customer.subscriptions.filter(status="active").exists():
            customer.status = "expired"
            customer.save()

        # Disable PPPoE or Hotspot on router
        disable_customer_access(customer)

        print(f"[EXPIRY] Disabled access for {customer.full_name}")



# =====================================================
# 2️⃣ SEND EXPIRY REMINDERS (SMS)
# =====================================================

def send_expiry_reminders():
    """
    Sends SMS reminders:
      - 3 days before expiry
      - 1 day before expiry
    Ensures reminders are not sent twice.
    """
    now = timezone.now()

    reminder_points = {
        "3_days": now + timedelta(days=3),
        "1_day": now + timedelta(days=1),
    }

    for reminder_type, target_date in reminder_points.items():

        # Find subscriptions expiring on that date
        subs_to_remind = Subscription.objects.filter(
            status="active",
            expiry_date__date=target_date.date(),   # Match the DATE only
        )

        for sub in subs_to_remind:
            customer = sub.customer

            # Avoid duplicate reminders
            if ExpiryReminderLog.objects.filter(
                subscription=sub,
                reminder_type=reminder_type,
            ).exists():
                continue

            expiry_str = sub.expiry_date.strftime("%Y-%m-%d")

            message = (
                f"Hello {customer.full_name}, "
                f"your internet package will expire on {expiry_str}. "
                f"Please renew early to avoid disconnection."
            )

            # Send SMS
            send_sms(customer.phone, message)

            # Log reminder
            ExpiryReminderLog.objects.create(
                subscription=sub,
                reminder_type=reminder_type,
            )

            print(f"[REMINDER] Sent {reminder_type} reminder to {customer.phone}")


def run_failover_migration():
    """
    For every active customer:
    - attempt to ensure they are provisioned on a working router
    - if their router is down, enable_customer_access() will move them automatically
    """
    qs = Customer.objects.filter(status="active").select_related("router")

    for customer in qs:
        try:
            # only migrate if they have an active subscription
            active_sub = customer.subscriptions.filter(status="active").exists()
            if not active_sub:
                continue

            enable_customer_access(customer)

        except Exception as e:
            print(f"[FAILOVER MIGRATION ERROR] {customer.full_name}: {e}")
            
from .router_service import safe_connect_router, migrate_customer_router

def auto_migrate_if_router_down():
    from .models import Customer

    customers = Customer.objects.filter(status="active").select_related("router")
    for c in customers:
        # must have active subscription
        if not c.subscriptions.filter(status="active").exists():
            continue

        if not c.router:
            # no router assigned → assign best
            migrate_customer_router(c, reason="no_router_assigned")
            continue

        # router health check
        api = safe_connect_router(c.router)
        if not api:
            migrate_customer_router(c, reason="router_down")


def snapshot_daily_usage():
    today = timezone.now().date()

    customers = Customer.objects.filter(status="active")

    for customer in customers:
        if customer.connection_type == "pppoe":
            usage = get_pppoe_live_usage(
                customer.router,
                customer.pppoe_username
            )
            if not usage or not usage.get("connected"):
                continue

            record, _ = UsageRecord.objects.get_or_create(
                customer=customer,
                connection_type="pppoe",
                date=today,
            )

            record.rx_bytes = usage["rx_bytes"]
            record.tx_bytes = usage["tx_bytes"]
            record.save()
           
def enforce_usage_caps():
    customers = Customer.objects.filter(status="active")

    for customer in customers:
        subscription = customer.subscriptions.filter(status="active").first()
        if not subscription:
            continue

        package = subscription.package
        cap_gb = customer.custom_data_cap_gb or package.monthly_data_cap_gb
        if not cap_gb:
            continue  # unlimited

        since = timezone.now().replace(day=1)

        if customer.connection_type == "pppoe":
            total = PPPoEUsageRecord.objects.filter(
                customer=customer,
                period_start__gte=since
            ).aggregate(
                used=Sum("download_bytes") + Sum("upload_bytes")
            )["used"] or 0

        else:
            total = HotspotUsageRecord.objects.filter(
                customer=customer,
                period_start__gte=since
            ).aggregate(
                used=Sum("download_bytes") + Sum("upload_bytes")
            )["used"] or 0

        used_gb = total / (1024**3)

        if used_gb >= cap_gb:
            disable_customer_access(customer)
            notify_customer(
                customer.phone,
                f"⚠ Data limit reached ({cap_gb}GB). Please renew or upgrade."
            )