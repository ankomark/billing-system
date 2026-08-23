"""
A phone that has just paid must be able to claim itself.

The complaint from the field: a customer buys a package, auto-connect fails,
they type the code they were sent, and the portal tells them the device belongs
to somebody else — about their own phone, on a code they paid for a minute ago.

Three ways the redemption path produced that answer, all of them about the
device rather than the code:

* The address was held by another account as a *secondary* device. The conflict
  check only ever looked at `Customer.hotspot_username`, so nothing saw the
  holder, and `CustomerDevice.objects.create()` walked into the unique
  constraint on (tenant, mac_address). An IntegrityError inside the atomic
  block is a 500, and the portal turns a 500 into "that code did not match".

* The address was held by an account whose binding was released — the release
  cleared `hotspot_username` and left the `CustomerDevice` row behind, so the
  very next statement hit the same constraint. The rollback undid the release
  too, which is why retrying never helped.

* The holder was the same human under a second phone number. Buying with a
  different M-Pesa number makes a second customer record, and the first one
  still had time left on it, so the check refused the device to its owner.

The rule now matches the one the device limit already uses: a binding is only
worth defending while the device is actually connected. Nobody standing on a
captive portal is.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    Customer, CustomerDevice, Package, RouterDevice, Subscription, Tenant,
    Voucher,
)
from billing.tenancy import tenant_context

PHONE = "3E:5E:04:A6:ED:BD"
LAPTOP = "6C:02:E0:08:B5:23"


class DeviceClaimTests(TestCase):

    VALIDATE = "/api/hotspot/validate/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.9.0.1",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="1 device", download_speed=5,
                upload_speed=2, price=Decimal("50.00"), duration_value=1,
                duration_unit="hours", monthly_data_cap_gb=0, is_hotspot=True,
                max_devices=1)

    # ---- helpers ---------------------------------------------------------

    def _customer(self, phone, *, hours=1, code=None, username=""):
        """A hotspot subscriber with live time on the clock."""
        with tenant_context(self.tenant):
            customer = Customer.objects.create(
                tenant=self.tenant, full_name=f"C {phone[-4:]}", phone=phone,
                connection_type="hotspot", router=self.router,
                hotspot_username=username)
            sub = Subscription.objects.create(
                tenant=self.tenant, customer=customer, package=self.package,
                status="active", expiry_date=timezone.now() + timedelta(hours=hours))
            voucher = None
            if code:
                voucher = Voucher.objects.create(
                    tenant=self.tenant, code=code, subscription=sub,
                    expires_at=timezone.now() + timedelta(hours=max(hours, 1)))
        return customer, sub, voucher

    def _expire(self, sub):
        Subscription.objects.all_tenants().filter(pk=sub.pk).update(
            status="expired", expiry_date=timezone.now() - timedelta(hours=1))

    def _bind(self, customer, mac, *, primary=True, subscription=None):
        """
        A device place as the redemption endpoint makes one — against the
        subscription that paid for it. A binding belonging to no package counts
        against no allowance, which is right for a device left over from one
        that expired, and wrong as a fixture for a customer whose package is
        live.
        """
        with tenant_context(self.tenant):
            if subscription is None:
                subscription = (
                    Subscription.objects.filter(customer=customer)
                    .order_by("-id").first())
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=customer,
                subscription=subscription, mac_address=mac)
            if primary:
                customer.hotspot_username = mac
                customer.save(update_fields=["hotspot_username"])

    def _redeem(self, code, mac, *, online=set()):
        """Present a code from a device. `online` is what the router reports."""
        with patch("billing.views.enable_customer_access"), \
             patch("billing.router_service.active_hotspot_macs",
                   return_value=online), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            return self.client.post(
                f"{self.VALIDATE}?t={self.tenant.public_token}",
                {"code": code, "mac_address": mac}, format="json")

    # ---- the address held by somebody else --------------------------------

    def test_a_device_left_behind_by_an_expired_account_is_claimable(self):
        """
        The release cleared the customer row and left the device row, so the
        create below it hit the unique constraint and returned a 500 — and the
        rollback undid the release, so it did so every time.
        """
        holder, sub, _ = self._customer("254700000101")
        self._bind(holder, PHONE)
        self._expire(sub)

        _, _, voucher = self._customer("254700000102", code="CLAIM1")

        resp = self._redeem("CLAIM1", PHONE)
        self.assertEqual(resp.status_code, 200, resp.data)

        with tenant_context(self.tenant):
            owners = list(CustomerDevice.objects.filter(mac_address=PHONE)
                          .values_list("customer_id", flat=True))
        self.assertEqual(len(owners), 1, "one device, one owner")
        holder.refresh_from_db()
        self.assertEqual(holder.hotspot_username, "")

    def test_a_device_held_as_somebody_elses_second_phone_is_claimable(self):
        """
        The conflict check read `hotspot_username` only, so a MAC held as any
        device but the first was invisible to it and reached the constraint.
        """
        holder, sub, _ = self._customer("254700000111")
        self._bind(holder, LAPTOP)
        self._bind(holder, PHONE, primary=False)
        self._expire(sub)

        _, _, voucher = self._customer("254700000112", code="CLAIM2")

        resp = self._redeem("CLAIM2", PHONE)
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_the_same_person_on_a_second_phone_number_is_not_a_stranger(self):
        """
        Buying with a different M-Pesa number makes a second customer record.
        The old one still has time on it, and the device is plainly not
        connected — the customer is standing on the login page.
        """
        holder, _, _ = self._customer("254700000121", hours=5)
        self._bind(holder, PHONE)

        _, _, _ = self._customer("254700000122", code="CLAIM3")

        resp = self._redeem("CLAIM3", PHONE, online=set())
        self.assertEqual(
            resp.status_code, 200,
            "a paying customer was refused their own phone")

    def test_a_device_connected_under_another_account_is_still_refused(self):
        """
        The one case where the binding is worth defending: the router says
        this address has a live session on the account that holds it.
        """
        holder, _, _ = self._customer("254700000131", hours=5)
        self._bind(holder, PHONE)

        self._customer("254700000132", code="CLAIM4")

        resp = self._redeem("CLAIM4", PHONE, online={PHONE})
        self.assertEqual(resp.status_code, 409)
        holder.refresh_from_db()
        self.assertEqual(holder.hotspot_username, PHONE)

    def test_the_release_is_audited(self):
        from billing.models import AccessAuditLog

        holder, sub, _ = self._customer("254700000141")
        self._bind(holder, PHONE)
        self._expire(sub)
        self._customer("254700000142", code="CLAIM5")

        self._redeem("CLAIM5", PHONE)

        with tenant_context(self.tenant):
            entry = (AccessAuditLog.objects.filter(customer=holder)
                     .order_by("-id").first())
        self.assertIsNotNone(entry, "a device changed hands with no record")
        self.assertIn(PHONE, entry.reason)

    # ---- letter case ------------------------------------------------------

    def test_one_phone_does_not_take_two_of_its_owners_device_slots(self):
        """
        RouterOS is not consistent about the case it reports an address in,
        and neither is a portal that was reloaded. Bound once in each spelling,
        a one-device package refuses its owner's only phone.
        """
        customer, _, _ = self._customer("254700000151", code="CASE1")
        self._bind(customer, PHONE.lower())

        resp = self._redeem("CASE1", PHONE.upper(), online={PHONE})
        self.assertEqual(
            resp.status_code, 200,
            "the same phone was counted twice over letter case")

        with tenant_context(self.tenant):
            self.assertEqual(
                CustomerDevice.objects.filter(customer=customer).count(), 1,
                "one phone, two device rows")

    def test_a_stranger_in_the_other_case_is_still_a_stranger(self):
        """Folding case must not fold two different devices together."""
        customer, _, _ = self._customer("254700000161", code="CASE2")
        self._bind(customer, LAPTOP)

        resp = self._redeem("CASE2", PHONE, online={LAPTOP})
        self.assertEqual(
            resp.status_code, 409,
            "a second device slipped past a one-device package")

    def test_a_block_is_not_escaped_by_the_other_spelling(self):
        """
        Folding two rows into one device must keep the answer that refuses.
        Keeping the older row would let a blocked handset connect by asking
        under the spelling nobody blocked.
        """
        customer, _, _ = self._customer("254700000181", code="BLOCK1")
        self._bind(customer, PHONE)
        with tenant_context(self.tenant):
            CustomerDevice.objects.all_tenants().filter(
                customer=customer).update(mac_address=PHONE.lower())
            CustomerDevice.objects.create(
                tenant=self.tenant, customer=customer, mac_address=PHONE,
                blocked=True, blocked_reason="stolen")

        resp = self._redeem("BLOCK1", PHONE)
        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertTrue(resp.data.get("blocked"))

    def test_the_status_endpoint_finds_a_device_bound_in_the_other_case(self):
        customer, _, _ = self._customer("254700000171")
        self._bind(customer, PHONE)
        from billing.models import Invoice
        Invoice.objects.all_tenants().filter(
            customer=customer).update(payment_status="paid")

        resp = self.client.get(
            "/api/hotspot/status/",
            {"mac": PHONE.lower(), "t": self.tenant.public_token})
        self.assertEqual(resp.data.get("status"), "active", resp.data)


class VoucherMintingTests(TestCase):
    """
    Six characters from a 36-symbol alphabet is a lot of codes and not an
    infinite number of them. `code` is unique, minting had no retry, and the
    mint happens inside the M-Pesa callback — so the one in N that collides
    took down the callback for a customer whose money had already left.
    """

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r2", ip_address="10.9.0.2",
                username="a", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="p", download_speed=5, upload_speed=2,
                price=Decimal("50.00"), duration_value=1, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True, max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Mint", phone="254700000201",
                connection_type="hotspot", router=self.router)
            self.sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=self.package,
                status="active", expiry_date=timezone.now() + timedelta(hours=1))

    def test_a_collision_does_not_cost_the_customer_their_voucher(self):
        from billing.models import mint_voucher

        with tenant_context(self.tenant):
            taken = Voucher.objects.create(
                tenant=self.tenant, code="AAAAAA", subscription=self.sub,
                expires_at=timezone.now() + timedelta(hours=1))

            codes = iter(["AAAAAA", "AAAAAA", "BBBBBB"])
            with patch("billing.models.generate_voucher_code",
                       side_effect=lambda: next(codes)):
                minted = mint_voucher(self.sub, self.sub.expiry_date)

        self.assertEqual(minted.code, "BBBBBB")
        self.assertNotEqual(minted.pk, taken.pk)

    def test_minting_still_fails_loudly_if_it_can_never_succeed(self):
        """
        Retrying forever would hide a broken generator. A bounded number of
        attempts, then the error, which the callback logs.
        """
        from django.db import IntegrityError

        from billing.models import mint_voucher

        with tenant_context(self.tenant):
            Voucher.objects.create(
                tenant=self.tenant, code="CCCCCC", subscription=self.sub,
                expires_at=timezone.now() + timedelta(hours=1))
            with patch("billing.models.generate_voucher_code",
                       return_value="CCCCCC"):
                with self.assertRaises(IntegrityError):
                    mint_voucher(self.sub, self.sub.expiry_date)
