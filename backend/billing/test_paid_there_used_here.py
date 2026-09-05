"""
End to end: who paid, and what the code then works on.

Two questions this answers by walking the whole path — purchase endpoint, the
payment that mints the voucher, then redemption on real devices:

1. A customer prompts a friend's phone for the M-Pesa payment. Does the code
   work on the customer's own television, and does it matter whether that
   friend's number already has a subscription of its own?

2. A two-device monthly is bought. Does it genuinely run on two devices, and
   may the second one be a laptop or a television rather than a second phone?

`HotspotPurchaseView` resolves the customer by the number that *pays*
(views.py, "Creates (or reuses) the customer by phone number"). So a friend's
number does not merely route the prompt — it decides whose account the
subscription lands on, and every device counted against it afterwards.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    Customer, CustomerDevice, Package, Payment, RouterDevice, Subscription,
    Tenant, Voucher,
)
from billing.tenancy import tenant_context

BUYER_PHONE = "254700000501"
FRIEND_PHONE = "254700000502"

PHONE = "5C:C5:D4:6B:37:E9"
TV = "B4:04:29:4B:4E:34"
LAPTOP = "6C:02:E0:08:B5:23"


class PaidThereUsedHereTests(TestCase):

    PURCHASE = "/api/hotspot/purchase/"
    VALIDATE = "/api/hotspot/validate/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.9.0.3",
                username="a", password="p")
            self.one_device = Package.objects.create(
                tenant=self.tenant, name="3hrs unlimited", download_speed=5,
                upload_speed=2, price=Decimal("20.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=0, is_hotspot=True,
                max_devices=1)
            self.two_device = Package.objects.create(
                tenant=self.tenant, name="monthly unlimited", download_speed=10,
                upload_speed=5, price=Decimal("1500.00"), duration_value=30,
                duration_unit="days", data_cap_mb=0, is_hotspot=True,
                max_devices=2)

    # ---- the real path ---------------------------------------------------

    def _buy(self, package, phone):
        """Purchase endpoint, exactly as the portal calls it."""
        with patch("billing.views.missing_mpesa_keys", return_value=[]), \
             patch("billing.views.initiate_stk_push_task"):
            response = self.client.post(
                f"{self.PURCHASE}?t={self.tenant.public_token}",
                {"phone": phone, "package_id": package.id}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        return response.data["reference"]

    def _pay(self, reference):
        """
        What the M-Pesa callback does: record the payment. Creating a Payment
        activates the subscription and mints the voucher (Payment.save).
        """
        from billing.models import Invoice

        with tenant_context(self.tenant), \
             patch("billing.router_service.enable_customer_access"), \
             patch("billing.router_service.pick_best_router_for_new_customer",
                   return_value=(self.router, "")), \
             patch("billing.notifications.notify_customer"):
            invoice = Invoice.objects.all_tenants().get(
                tenant=self.tenant, invoice_number=reference)
            Payment.objects.create(
                tenant=self.tenant, customer=invoice.subscription.customer,
                subscription=invoice.subscription,
                amount=invoice.total_amount, method="mpesa",
                reference=f"R{invoice.id}")
            voucher = (
                Voucher.objects.all_tenants()
                .filter(subscription=invoice.subscription)
                .latest("id"))
            return voucher.code, invoice.subscription.customer

    def _redeem(self, code, mac, *, online=frozenset(), auto=False):
        body = {"code": code, "mac_address": mac}
        if auto:
            body["auto"] = True
        with patch("billing.views.enable_customer_access"), \
             patch("billing.router_service.active_hotspot_macs",
                   return_value=set(online)), \
             patch("billing.router_service.safe_connect_router",
                   return_value=None):
            return self.client.post(
                f"{self.VALIDATE}?t={self.tenant.public_token}",
                body, format="json")

    def _macs(self, customer):
        with tenant_context(self.tenant):
            return set(
                CustomerDevice.objects.all_tenants()
                .filter(customer=customer)
                .values_list("mac_address", flat=True))

    # ---- 1. paying with somebody else's phone ----------------------------

    def test_a_friends_phone_pays_and_the_code_works_on_the_buyers_tv(self):
        """
        The buyer is standing at their television with no M-Pesa balance, so
        they put a friend's number into the prompt. The code that comes back
        has to work here, on this television.
        """
        code, _ = self._pay(self._buy(self.one_device, FRIEND_PHONE))

        response = self._redeem(code, TV)

        self.assertEqual(response.status_code, 200, response.data)

    def test_the_buyers_own_active_subscription_is_untouched(self):
        """
        The buyer's own number already has a package running. Paying with a
        friend's number must not disturb it — different number, different
        account, its own devices.
        """
        own_code, own_customer = self._pay(
            self._buy(self.one_device, BUYER_PHONE))
        self.assertEqual(self._redeem(own_code, PHONE).status_code, 200)

        friend_code, friend_customer = self._pay(
            self._buy(self.one_device, FRIEND_PHONE))
        response = self._redeem(friend_code, TV, online={PHONE})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(own_customer.pk, friend_customer.pk)
        self.assertEqual(self._macs(own_customer), {PHONE})
        self.assertEqual(self._macs(friend_customer), {TV})

    def test_a_friends_own_package_does_not_compete_for_the_devices(self):
        """
        Buying on a friend's number puts the package on the friend's account,
        because the number that pays is the account. Their own package must not
        therefore swallow it: two payments, two allowances, both connected at
        once — even though one account holds both.
        """
        friend_code, friend_customer = self._pay(
            self._buy(self.one_device, FRIEND_PHONE))
        self.assertEqual(self._redeem(friend_code, PHONE).status_code, 200)

        bought_code, bought_customer = self._pay(
            self._buy(self.one_device, FRIEND_PHONE))
        response = self._redeem(bought_code, TV, online={PHONE})

        self.assertEqual(bought_customer.pk, friend_customer.pk,
                         "the same number is the same account")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            self._macs(friend_customer), {PHONE, TV},
            "the friend's phone kept the place its own package paid for")

    def test_a_friend_buys_on_his_own_phone_using_my_number_to_pay(self):
        """
        The complaint, exactly as customers report it.

        My number has a three-hour package running and my phone is connected.
        My friend has no M-Pesa balance, so he buys on *his* phone and puts
        *my* number into the prompt. The payment goes through. The code does
        not work — not on his phone, not on anything.

        `HotspotPurchaseView` resolves the customer by the number that pays, so
        his package lands on my account, and my connected phone is holding the
        only device place it allows. He paid and got nothing, and neither of us
        did anything wrong.
        """
        mine, me = self._pay(self._buy(self.one_device, BUYER_PHONE))
        self.assertEqual(self._redeem(mine, PHONE, auto=True).status_code, 200)

        his, whose = self._pay(self._buy(self.one_device, BUYER_PHONE))
        response = self._redeem(his, TV, online={PHONE})

        self.assertEqual(whose.pk, me.pk, "my number, so my account")
        self.assertEqual(
            response.status_code, 200,
            "a package he paid for must work on the device he bought it on, "
            "whoever's number settled the bill")

        # And the half that matters just as much: two packages were paid for,
        # so two devices are entitled to be connected. Letting his phone on by
        # throwing mine off is not the fix — I bought three hours and am in the
        # middle of them.
        self.assertEqual(
            self._macs(me), {PHONE, TV},
            "his phone took my place instead of taking one of its own")

    # ---- 2. the two-device monthly ---------------------------------------

    def test_two_device_monthly_runs_on_a_phone_and_a_television(self):
        code, customer = self._pay(self._buy(self.two_device, BUYER_PHONE))

        self.assertEqual(
            self._redeem(code, PHONE, auto=True).status_code, 200)
        response = self._redeem(code, TV, online={PHONE})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._macs(customer), {PHONE, TV})

    def test_two_device_monthly_runs_on_a_phone_and_a_laptop(self):
        """The second device is not required to be a phone."""
        code, customer = self._pay(self._buy(self.two_device, BUYER_PHONE))

        self.assertEqual(
            self._redeem(code, PHONE, auto=True).status_code, 200)
        response = self._redeem(code, LAPTOP, online={PHONE})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._macs(customer), {PHONE, LAPTOP})

    def test_the_third_device_is_refused_on_a_two_device_monthly(self):
        """Two is two. Both online, so nothing is free for a third."""
        code, customer = self._pay(self._buy(self.two_device, BUYER_PHONE))
        self.assertEqual(self._redeem(code, PHONE).status_code, 200)
        self.assertEqual(
            self._redeem(code, TV, online={PHONE}).status_code, 200)

        response = self._redeem(code, LAPTOP, online={PHONE, TV})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._macs(customer), {PHONE, TV})

    def test_a_one_device_code_works_alongside_a_two_device_monthly(self):
        """
        Customer 409 on skylink holds a two-device monthly *and* a one-device
        week pass, with two devices bound. Their week pass used to be refused
        by the very devices their monthly entitled them to — the allowance came
        from the code typed, the places were counted across the whole account.

        Each package now carries its own places, so the third device connects
        on the pass that paid for it while the monthly keeps both of its own.
        """
        monthly, customer = self._pay(self._buy(self.two_device, BUYER_PHONE))
        self.assertEqual(self._redeem(monthly, PHONE).status_code, 200)
        self.assertEqual(
            self._redeem(monthly, TV, online={PHONE}).status_code, 200)

        week, again = self._pay(self._buy(self.one_device, BUYER_PHONE))
        response = self._redeem(week, LAPTOP, online={PHONE, TV})

        self.assertEqual(again.pk, customer.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._macs(customer), {PHONE, TV, LAPTOP})

    # ---- 3. buying twice on one number -----------------------------------

    def test_buying_a_second_package_on_the_same_number(self):
        """
        Customer 219 bought five three-hour packages in six minutes on
        2026-08-23, all still active. Somebody paying again because they could
        not get online must at least be able to use what they just bought.
        """
        first_code, customer = self._pay(
            self._buy(self.one_device, BUYER_PHONE))
        self.assertEqual(
            self._redeem(first_code, PHONE, auto=True).status_code, 200)

        second_code, again = self._pay(
            self._buy(self.one_device, BUYER_PHONE))
        response = self._redeem(second_code, TV, online={PHONE})

        self.assertEqual(again.pk, customer.pk)
        self.assertEqual(response.status_code, 200, response.data)
