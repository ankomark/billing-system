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

        # Count and record sign-ins that go through authenticate() outside
        # the API login view -- the Django admin, chiefly. See
        # billing/login_security.py.
        from django.contrib.auth.signals import user_logged_in, user_login_failed
        from billing.login_security import on_admin_logged_in, on_login_failed
        user_login_failed.connect(on_login_failed, dispatch_uid="billing.login_failed")
        user_logged_in.connect(on_admin_logged_in, dispatch_uid="billing.logged_in")

        # The Django admin's login asks for a two-step code too.
        from django.contrib import admin
        from billing.admin_login import MFAAdminAuthenticationForm
        admin.site.login_form = MFAAdminAuthenticationForm
