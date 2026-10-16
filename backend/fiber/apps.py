from django.apps import AppConfig


class FiberConfig(AppConfig):
    """
    Smart Mapping: the fiber plant behind each operator's routers.

    Kept out of `billing` on purpose. Billing is about who has paid for what;
    this is about glass, splitters and the boxes on customers' walls. It reads
    billing's customers and subscriptions to say who an outage hits, and calls
    billing's provisioning to compensate them, but billing never imports it.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "fiber"
    verbose_name = "Fiber (Smart Mapping)"
