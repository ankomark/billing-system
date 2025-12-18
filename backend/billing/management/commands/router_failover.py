from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import RouterDevice, Customer
from billing.router_service import is_router_reachable, migrate_customer_router


class Command(BaseCommand):
    help = "Checks router health and auto-migrates active customers when router is down."

    def handle(self, *args, **options):
        routers = RouterDevice.objects.filter(is_active=True).order_by("priority")

        for router in routers:
            online = is_router_reachable(router)
            if online:
                continue

            self.stdout.write(self.style.WARNING(f"[FAILOVER] Router DOWN: {router.name}"))

            # Customers currently assigned to this router
            customers = Customer.objects.filter(router=router)

            migrated = 0
            failed = 0

            for c in customers:
                ok, msg = migrate_customer_router(c, from_router_id=router.id)
                if ok:
                    migrated += 1
                else:
                    failed += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"[FAILOVER] {router.name} -> migrated={migrated}, failed={failed}"
                )
            )
