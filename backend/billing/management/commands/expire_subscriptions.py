from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import Subscription
from billing.router_service import disable_customer_access


class Command(BaseCommand):
    help = "Expire outdated subscriptions and disable router access"

    def handle(self, *args, **kwargs):
        now = timezone.now()

        expired_subscriptions = Subscription.objects.filter(
            status="active",
            expiry_date__lte=now,
        )

        expired_count = 0

        for subscription in expired_subscriptions:
            customer = subscription.customer

            # ✅ Expire subscription
            subscription.status = "expired"
            subscription.save(update_fields=["status"])

            # ✅ Expire customer
            customer.status = "expired"
            customer.save(update_fields=["status"])

            # ✅ Disable router access (PPPoE / Hotspot)
            disable_customer_access(customer)

            expired_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Subscription expiry check completed — {expired_count} expired"
            )
        )
