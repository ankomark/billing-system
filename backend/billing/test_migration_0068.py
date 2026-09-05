"""
The 0068 conversion, exercised against real rows rather than read.

A migration that renames a column is easy to eyeball and easy to get wrong in
the one way that matters: silently converting nothing, or converting into the
wrong unit. This migrates a database up to 0067, writes caps in gigabytes,
runs 0068, and checks what came out the other side.
"""

from decimal import Decimal

from django.db.migrations.executor import MigrationExecutor
from django.db import connection
from django.test import TransactionTestCase


class Migration0068Tests(TransactionTestCase):
    # The data migration reads every Package/Customer row, so the tables must
    # actually exist at 0067 — which means migrating rather than trusting the
    # test runner's already-migrated schema.
    migrate_from = [("billing", "0067_routerdevice_public_token")]
    migrate_to = [("billing", "0068_data_cap_mb")]

    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor

    def test_gigabyte_caps_become_megabyte_caps(self):
        executor = self._migrate(self.migrate_from)
        old = executor.loader.project_state(self.migrate_from).apps

        Tenant = old.get_model("billing", "Tenant")
        Package = old.get_model("billing", "Package")
        Customer = old.get_model("billing", "Customer")

        tenant = Tenant.objects.create(name="Skylink", slug="skylink-mig")

        unlimited = Package.objects.create(
            tenant=tenant, name="Unlimited", download_speed=10,
            upload_speed=5, price=Decimal("100.00"), duration_value=30,
            duration_unit="days", monthly_data_cap_gb=0)
        five_gb = Package.objects.create(
            tenant=tenant, name="5GB", download_speed=10, upload_speed=5,
            price=Decimal("500.00"), duration_value=30, duration_unit="days",
            monthly_data_cap_gb=5)

        inherits = Customer.objects.create(
            tenant=tenant, full_name="Inherits", phone="254700000001",
            connection_type="pppoe", custom_data_cap_gb=None)
        overridden = Customer.objects.create(
            tenant=tenant, full_name="Overridden", phone="254700000002",
            connection_type="pppoe", custom_data_cap_gb=2)

        # ── the migration under test ─────────────────────────────────────────
        executor = self._migrate(self.migrate_to)
        new = executor.loader.project_state(self.migrate_to).apps

        Package = new.get_model("billing", "Package")
        Customer = new.get_model("billing", "Customer")

        self.assertEqual(
            Package.objects.get(pk=unlimited.pk).data_cap_mb, 0,
            "unlimited must stay unlimited in either unit")
        self.assertEqual(
            Package.objects.get(pk=five_gb.pk).data_cap_mb, 5 * 1024,
            "5 GB did not become 5120 MB — the backfill converted nothing, "
            "or converted into the wrong unit")

        self.assertIsNone(
            Customer.objects.get(pk=inherits.pk).custom_data_cap_mb,
            "an absent override must stay absent, not become 0 — 0 means "
            "'uncapped on purpose', which is a different thing")
        self.assertEqual(
            Customer.objects.get(pk=overridden.pk).custom_data_cap_mb, 2 * 1024)

    def tearDown(self):
        # Leave the database where the rest of the suite expects it.
        self._migrate([("billing", "0068_data_cap_mb")])
        super().tearDown()
