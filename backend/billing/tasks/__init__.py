from .router_health import check_router_health
from .auto_failover import auto_failover_migrate


def run_failover_cycle():
    """
    Full failover cycle:
    1) Check router health
    2) Auto-migrate affected customers
    """
    check_router_health()
    auto_failover_migrate()
