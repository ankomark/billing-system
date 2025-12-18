from django.utils import timezone
from billing.models import RouterDevice
from billing.router_service import safe_connect_router


def check_router_health():
    """
    Periodically checks all routers and updates:
    - is_online
    - last_seen
    - last_error
    """
    routers = RouterDevice.objects.filter(is_active=True)

    for router in routers:
        api = safe_connect_router(router)

        if api:
            # already updated inside safe_connect_router
            print(f"[HEALTH] {router.name} ONLINE")
        else:
            print(f"[HEALTH] {router.name} OFFLINE")
