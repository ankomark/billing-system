from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        import billing.signals  # 👈 REQUIRED

        # Apply the tenant scope to every new database connection. A request
        # usually opens its connection lazily, so without this its first query
        # would run before any scope was set and RLS would allow everything.
        from django.db.backends.signals import connection_created
        from billing.tenancy import apply_scope_to_new_connection
        connection_created.connect(
            apply_scope_to_new_connection,
            dispatch_uid="billing.apply_tenant_scope",
        )
