from billing.models import Customer, RouterDevice
from billing.router_service import migrate_customer_router


def auto_failover_migrate():
    """
    Automatically migrate customers away from offline routers.
    """
    offline_routers = RouterDevice.objects.filter(
        is_active=True,
        is_online=False,
    )

    if not offline_routers.exists():
        return

    for router in offline_routers:
        customers = Customer.objects.filter(
            router=router,
            status="active",
        )

        for customer in customers:
            success, message = migrate_customer_router(
                customer,
                reason="auto_failover"
            )

            if success:
                print(f"[AUTO-FAILOVER] {customer.full_name}: {message}")
            else:
                print(f"[AUTO-FAILOVER FAILED] {customer.full_name}: {message}")
