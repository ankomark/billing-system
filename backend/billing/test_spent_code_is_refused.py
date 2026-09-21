"""
A code whose data is gone must be refused at the portal, not accepted and then
quietly never granted.

check_cap suspends a spent subscription, but only the one
_billable_subscription picks for the customer. A customer holding several
stacked packages can have one whose allowance is used up and whose status
still reads "active". validate trusted the status; the grant reads usage
itself and refused. Seen on 2026-09-21, customer 386, subscription 18562:

    14:41  validate -> 202  "Setting up your connection"
    14:41  refusing to provision ... 0 bytes left on subscription 18562
    ...    retried at 5s, 20s, 60s, 240s, 960s
    15:03  Could not put Hotspot 2125 on the network after 6 attempts --
           no router was reachable. They have paid.

The portal waited on a grant that was never going to happen, and the operator
was told the router was down when it was not.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    Customer, CustomerDevice, HotspotUsageRecord, Package, RouterDevice,
    Subscription, Tenant, Voucher,
)
from billing.services.usage import MB
from billing.tasks.provisioning import ensure_customer_access_task
from billing.tenancy import tenant_context

MAC = "AA:BB:CC:00:66:77"


class StackedPackagesBase(TestCase):
    """
    A small bundle and a large one held at once. The large one is what the cap
    sweep watches, so the small one stays "active" after its data is gone.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")
        now = timezone.now()
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.7",
                username="u", password="p")
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Wanjiru", phone="254700777888",
                connection_type="hotspot", router=self.router)
            self.small = self._subscription("1 week - 300MB", 300, now)
            self.large = self._subscription("2 weeks - 60GB", 60000, now)
            Voucher.objects.create(
                tenant=self.tenant, code="WIFI-SPENT1",
                subscription=self.small, expires_at=now + timedelta(days=7))

    def _subscription(self, name, cap_mb, now):
        package = Package.objects.create(
            tenant=self.tenant, name=name, download_speed=5, upload_speed=2,
            price=Decimal("100.00"), duration_value=7, duration_unit="days",
            data_cap_mb=cap_mb, is_hotspot=True, max_devices=1)
        sub = Subscription.objects.create(
            tenant=self.tenant, customer=self.customer, package=package,
            status="active", start_date=now - timedelta(minutes=5),
            expiry_date=now + timedelta(days=7))
        invoice = sub.invoice
        invoice.payment_status = "paid"
        invoice.save(update_fields=["payment_status"])
        return sub

    def spend(self, byte_count):
        with tenant_context(self.tenant):
            HotspotUsageRecord.objects.create(
                tenant=self.tenant, customer=self.customer, router=self.router,
                period_start=timezone.now(), period_end=timezone.now(),
                download_bytes=int(byte_count), upload_bytes=0)

    def validate(self):
        return APIClient().post(
            f"/api/hotspot/validate/?t={self.tenant.public_token}",
            {"code": "WIFI-SPENT1", "mac_address": MAC,
             "provision_async": True},
            format="json")


class ThePortalIsToldTheTruth(StackedPackagesBase):

    def test_a_spent_code_is_refused_as_capped(self):
        self.spend(400 * MB)
        with patch("billing.views.enable_customer_access") as inline, \
             patch("billing.tasks.provisioning."
                   "ensure_customer_access_task") as task:
            response = self.validate()

        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            response.data.get("capped"),
            "the portal words a capped refusal differently -- 'buy another', "
            "not 'retype it'")
        task.delay.assert_not_called()
        inline.assert_not_called()

    def test_a_spent_code_does_not_take_a_device_place(self):
        self.spend(400 * MB)
        with patch("billing.views.enable_customer_access"), \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            self.validate()

        with tenant_context(self.tenant):
            self.assertFalse(
                CustomerDevice.objects.filter(customer=self.customer).exists())

    def test_a_code_with_data_left_is_still_accepted(self):
        """The check must not refuse anyone who has something to be given."""
        self.spend(100 * MB)
        with patch("billing.views.enable_customer_access"), \
             patch("billing.tasks.provisioning.ensure_customer_access_task"):
            response = self.validate()

        self.assertEqual(response.status_code, 202)


class TheTaskDoesNotRetryARefusal(StackedPackagesBase):

    def test_a_spent_subscription_is_neither_retried_nor_reported(self):
        self.spend(400 * MB)
        with patch("billing.router_service.enable_customer_access") as grant, \
             patch("billing.tasks.provisioning._report_failure") as report, \
             patch.object(ensure_customer_access_task, "retry",
                          side_effect=RuntimeError("retried")) as retry:
            result = ensure_customer_access_task(
                self.customer.id, reason="voucher",
                subscription_id=self.small.id)

        self.assertFalse(result)
        grant.assert_not_called()
        retry.assert_not_called()
        report.assert_not_called()

    def test_a_subscription_with_data_left_is_still_granted(self):
        self.spend(100 * MB)
        with patch("billing.router_service.enable_customer_access",
                   return_value=True) as grant:
            result = ensure_customer_access_task(
                self.customer.id, reason="voucher",
                subscription_id=self.small.id)

        self.assertTrue(result)
        grant.assert_called_once()
