"""Fixtures shared by the fiber tests. Imported, never collected as tests."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone

from billing.models import (Customer, CustomerDevice, Invoice, Package, RouterDevice,
                            Subscription, Tenant)
from billing.tenancy import tenant_context

from fiber.models import Olt


class FiberFixture:
    """Mixin: a tenant, a router, a simulated OLT and helpers to make customers."""

    def make_world(self, onus_per_port=(6, 4, 0, 0)):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="ccr", ip_address="10.9.7.1", username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="1 week", download_speed=5, upload_speed=2,
                price=Decimal("140.00"), duration_value=1, duration_unit="weeks",
                data_cap_mb=0, is_hotspot=True)
            self.olt = Olt.objects.create(
                tenant=self.tenant, name="VSOL-1", vendor=Olt.VENDOR_SIMULATED,
                mgmt_ip="10.0.0.2", router=self.router, uplink_interface="sfp-sfpplus3",
                profile_overrides={"sim": {"onus_per_port": list(onus_per_port)}})
        self._n = 0

    def sim(self, **kw):
        sim = dict(self.olt.profile_overrides.get("sim", {}))
        sim.update(kw)
        self.olt.profile_overrides = {**self.olt.profile_overrides, "sim": sim}
        self.olt.save(update_fields=["profile_overrides"])

    def customer_with_device(self, mac, *, paid=True, hours_left=100):
        self._n += 1
        with tenant_context(self.tenant):
            c = Customer.objects.create(
                tenant=self.tenant, full_name=f"Cust {self._n}", phone=f"2547000099{self._n:02d}",
                connection_type="hotspot", router=self.router, hotspot_username=mac, status="active")
            start = timezone.now() - timedelta(hours=168 - hours_left)
            with patch("billing.models.notify_customer"):
                sub = Subscription.objects.create(
                    tenant=self.tenant, customer=c, package=self.package, status="active",
                    start_date=start, expiry_date=start + timedelta(weeks=1))
            if paid:
                Invoice.objects.filter(subscription=sub).update(payment_status="paid")
            CustomerDevice.objects.create(tenant=self.tenant, customer=c, mac_address=mac)
        return c, sub
