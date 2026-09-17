"""
A second package bought while the first is still live belongs to the phone that
bought it.

Every grant path asked `granting_subscription(customer)` -- "the customer's
longest paid package" -- and then provisioned the devices bound to *that*. For
somebody holding one package that is the same question. For somebody holding
two it is the wrong one, in both directions:

  * the phone that just paid gets nothing, because the longest package belongs
    to another handset. On 2026-09-15 customer 32 paid 10/- for three hours at
    16:06 while a three-week package sat on a second phone; the grant went to
    the second phone five times, and the one at the portal was told "invalid
    username or password" each time, then once more on the refund that was
    issued when it would not work.

  * a handset whose own package is long over is topped back up, because the
    sweep that keeps `limit-uptime` honest asked the same question. Five
    retired phones of that same customer were each being given the sixteen days
    left on the three-week package.

So each package covers the devices it was redeemed on. Buying again while
covered is allowed -- an operator has no business deciding why somebody wants a
second package -- and the first package keeps working on its own phone. The
customer chooses which code a phone runs on by typing it there.

The one borrowing that stays is a package nobody has redeemed yet: paid for and
not yet typed in anywhere, which is what a renewal looks like between the
payment and the code. That package is available to a device with nothing else,
because the alternative is a customer who has paid and gets nothing.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    Customer, CustomerDevice, Invoice, Package, RouterDevice, Subscription,
    Tenant, Voucher,
)
from billing.router_service import enable_customer_access, macs_to_grant
from billing.services.entitlement import subscription_for_device
from billing.tasks.subscription_tasks import enforce_subscription_expiry
from billing.tenancy import tenant_context

PHONE_A = "AA:11:22:33:44:01"      # the long package's phone
PHONE_B = "BB:11:22:33:44:02"      # the phone that buys three hours
VALIDATE = "/api/hotspot/validate/"


class TwoPackagesTwoPhones(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="two-pkg-r", ip_address="10.9.7.1",
                username="u", password="p", is_active=True, is_online=True)
            self.long = Package.objects.create(
                tenant=self.tenant, name="3 weeks - 40GB", download_speed=5,
                upload_speed=2, price=Decimal("250.00"), duration_value=3,
                duration_unit="weeks", data_cap_mb=40000, is_hotspot=True,
                max_devices=1)
            self.short = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=5,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True,
                max_devices=1)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Two Phones",
                phone="254700770001", connection_type="hotspot",
                router=self.router, status="active", hotspot_username=PHONE_A)

    # ---- fixtures --------------------------------------------------------

    def _subscribe(self, package, *, expires_in, paid=True, mac=None,
                   code=None):
        with tenant_context(self.tenant):
            sub = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer, package=package,
                status="active")
            Subscription.objects.filter(pk=sub.pk).update(
                expiry_date=timezone.now() + expires_in)
            Invoice.objects.filter(subscription=sub).update(
                payment_status="paid" if paid else "pending")
            sub.refresh_from_db()
            if mac:
                CustomerDevice.objects.create(
                    tenant=self.tenant, customer=self.customer,
                    subscription=sub, mac_address=mac)
            if code:
                Voucher.objects.create(
                    tenant=self.tenant, code=code, subscription=sub,
                    expires_at=sub.expiry_date)
        return sub

    def _grant(self, subscription=None):
        """Provision, capturing the addresses written to the router."""
        with patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.enable_hotspot") as enable, \
             patch("billing.router_service.retry_mac_login"), \
             tenant_context(self.tenant):
            granted = enable_customer_access(self.customer, subscription)
        return granted, [c.args[2] for c in enable.call_args_list]

    # ---- the phone that paid is the phone that is granted ----------------

    def test_the_phone_that_redeemed_is_the_one_provisioned(self):
        """
        The whole bug. Both packages are live; the code just typed on PHONE_B
        is the one being granted, so PHONE_B is what must reach the router.
        """
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        short = self._subscribe(self.short, expires_in=timedelta(hours=3),
                                mac=PHONE_B)

        granted, macs = self._grant(short)

        self.assertTrue(granted)
        self.assertEqual(macs, [PHONE_B],
                         "the grant went to the other phone, and the one that "
                         "paid was left with no account to log in with")

    def test_the_long_package_still_serves_its_own_phone(self):
        """Buying for a second phone must not disturb the first."""
        long_sub = self._subscribe(self.long, expires_in=timedelta(days=21),
                                   mac=PHONE_A)
        self._subscribe(self.short, expires_in=timedelta(hours=3), mac=PHONE_B)

        granted, macs = self._grant(long_sub)

        self.assertTrue(granted)
        self.assertEqual(macs, [PHONE_A])

    def test_naming_no_package_still_grants_the_longest(self):
        """
        Every caller that does not know which package it is acting for -- the
        admin resume, tenant re-enabling, failover recovery -- keeps the
        behaviour it had.
        """
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        self._subscribe(self.short, expires_in=timedelta(hours=3), mac=PHONE_B)

        granted, macs = self._grant()

        self.assertTrue(granted)
        self.assertEqual(macs, [PHONE_A])

    # ---- what may be named -----------------------------------------------

    def test_an_unpaid_package_is_refused_even_when_named(self):
        """Naming a package must not be a way around the payment check."""
        unpaid = self._subscribe(self.short, expires_in=timedelta(hours=3),
                                 paid=False, mac=PHONE_B)

        granted, macs = self._grant(unpaid)

        self.assertFalse(granted)
        self.assertEqual(macs, [])

    def test_an_expired_package_is_refused_even_when_named(self):
        expired = self._subscribe(self.short, expires_in=timedelta(hours=-1),
                                  mac=PHONE_B)

        granted, macs = self._grant(expired)

        self.assertFalse(granted)
        self.assertEqual(macs, [])

    def test_another_customer_s_package_is_refused(self):
        with tenant_context(self.tenant):
            stranger = Customer.objects.create(
                tenant=self.tenant, full_name="Someone Else",
                phone="254700770002", connection_type="hotspot",
                router=self.router, status="active")
            theirs = Subscription.objects.create(
                tenant=self.tenant, customer=stranger, package=self.long,
                status="active",
                expiry_date=timezone.now() + timedelta(days=21))
            Invoice.objects.filter(subscription=theirs).update(
                payment_status="paid")
            theirs.refresh_from_db()

        granted, macs = self._grant(theirs)

        self.assertFalse(granted)
        self.assertEqual(macs, [])

    # ---- which package a device is on ------------------------------------

    def test_a_device_is_on_the_package_it_was_redeemed_against(self):
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        short = self._subscribe(self.short, expires_in=timedelta(hours=3),
                                mac=PHONE_B)

        with tenant_context(self.tenant):
            self.assertEqual(
                subscription_for_device(self.customer, PHONE_B).pk, short.pk)

    def test_a_device_whose_package_ended_is_on_nothing(self):
        """Not on the other phone's package, which is what it used to borrow."""
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        self._subscribe(self.short, expires_in=timedelta(hours=-1), mac=PHONE_B)

        with tenant_context(self.tenant):
            self.assertIsNone(
                subscription_for_device(self.customer, PHONE_B))

    def test_a_renewal_nobody_has_redeemed_yet_still_covers_a_device(self):
        """
        Paid, and the code not yet typed anywhere. This is the case the grant
        has always had to carry: the customer has paid and their phone must
        come back on.
        """
        self._subscribe(self.short, expires_in=timedelta(hours=-1), mac=PHONE_A)
        renewal = self._subscribe(self.long, expires_in=timedelta(days=21))

        with tenant_context(self.tenant):
            self.assertEqual(
                subscription_for_device(self.customer, PHONE_A).pk, renewal.pk)

    def test_a_blocked_device_is_on_nothing(self):
        sub = self._subscribe(self.long, expires_in=timedelta(days=21),
                              mac=PHONE_A)
        with tenant_context(self.tenant):
            CustomerDevice.objects.filter(subscription=sub).update(blocked=True)
            self.assertIsNone(subscription_for_device(self.customer, PHONE_A))

    # ---- a payment that arrives before the code is typed ------------------

    def test_a_new_package_does_not_take_over_a_phone_on_a_longer_one(self):
        """
        Between the payment and the code there is no device on the new package,
        and the grant falls back to the customer's addresses. Taking PHONE_A
        would rewrite the three-week account down to three hours and 3 GB.
        """
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        short = self._subscribe(self.short, expires_in=timedelta(hours=3))

        with tenant_context(self.tenant):
            macs = macs_to_grant(self.customer, short)

        self.assertEqual(macs, [])

    def test_a_top_up_on_the_same_phone_still_takes_it(self):
        """
        The package that ends sooner is the one being replaced, so the phone
        moves onto the new one. This is a renewal, not a second phone.
        """
        self._subscribe(self.short, expires_in=timedelta(minutes=20),
                        mac=PHONE_A)
        longer = self._subscribe(self.long, expires_in=timedelta(days=21))

        with tenant_context(self.tenant):
            macs = macs_to_grant(self.customer, longer)

        self.assertEqual([m.upper() for m in macs], [PHONE_A])

    # ---- expiry ----------------------------------------------------------

    def _expire_sweep(self):
        with patch("billing.tasks.subscription_tasks.disable_customer_task"
                   ) as disable, \
             patch("billing.tasks.router_tasks.kick_device_task.delay"
                   ) as kick:
            enforce_subscription_expiry.run()
        return ([c.args[0] for c in disable.delay.call_args_list],
                [c.args[1] for c in kick.call_args_list])

    def test_the_second_phone_comes_off_when_its_own_package_ends(self):
        """
        "Another subscription is live" was asked of the customer, so the phone
        whose three hours had ended stayed on the router, spending whatever
        was left of its limit-uptime -- which counts connected time, not the
        clock.
        """
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        self._subscribe(self.short, expires_in=timedelta(minutes=-1),
                        mac=PHONE_B)

        disabled, kicked = self._expire_sweep()

        self.assertEqual([m.upper() for m in kicked], [PHONE_B])
        self.assertNotIn(self.customer.pk, disabled,
                         "the phone still holding three weeks was cut off")

    def test_a_phone_whose_renewal_is_already_paid_stays_on(self):
        """
        The package on this phone has ended and the next one is paid for but
        not yet typed in anywhere. Taking the phone off would cut off somebody
        who has already paid to stay on.
        """
        self._subscribe(self.short, expires_in=timedelta(minutes=-1),
                        mac=PHONE_A)
        self._subscribe(self.long, expires_in=timedelta(days=21))

        disabled, kicked = self._expire_sweep()

        self.assertEqual(kicked, [])
        self.assertNotIn(self.customer.pk, disabled)

    def test_nothing_left_at_all_still_disconnects_the_customer(self):
        """The ordinary single-package case, unchanged."""
        self._subscribe(self.short, expires_in=timedelta(minutes=-1),
                        mac=PHONE_B)

        disabled, kicked = self._expire_sweep()

        self.assertIn(self.customer.pk, disabled)
        self.assertEqual(kicked, [], "the per-customer disable covers these")

    # ---- the portal ------------------------------------------------------

    def test_redeeming_the_short_code_provisions_the_phone_holding_it(self):
        """
        End to end through the endpoint the customer actually reaches, which is
        where this was reported: a valid code, a phone that had just paid, and
        "invalid username or password" at the router because the account was
        written for a different handset.
        """
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        self._subscribe(self.short, expires_in=timedelta(hours=3),
                        code="SHORT1")

        with patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.enable_hotspot") as enable, \
             patch("billing.router_service.retry_mac_login"), \
             patch("billing.router_service.safe_connect_router",
                   return_value=MagicMock()), \
             patch("billing.router_service.active_hotspot_macs",
                   return_value=set()):
            resp = APIClient().post(
                f"{VALIDATE}?t={self.tenant.public_token}",
                {"code": "SHORT1", "mac_address": PHONE_B}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual([c.args[2] for c in enable.call_args_list], [PHONE_B])

    def test_reconnect_answers_for_the_device_that_asks(self):
        """
        PHONE_B's three hours are over; PHONE_A's three weeks are not. The
        endpoint used to answer "allowed" to PHONE_B on the strength of
        PHONE_A's package, then provision PHONE_A -- so the portal submitted a
        login for an account that did not exist.
        """
        self._subscribe(self.long, expires_in=timedelta(days=21), mac=PHONE_A)
        self._subscribe(self.short, expires_in=timedelta(hours=-1), mac=PHONE_B)

        with patch("billing.router_service.pick_working_router",
                   return_value=(self.router, MagicMock())), \
             patch("billing.router_service.enable_hotspot"), \
             patch("billing.router_service.retry_mac_login"):
            refused = APIClient().post("/api/hotspot/reconnect/", {
                "t": self.tenant.public_token, "mac": PHONE_B}, format="json")
            allowed = APIClient().post("/api/hotspot/reconnect/", {
                "t": self.tenant.public_token, "mac": PHONE_A}, format="json")

        self.assertEqual(refused.status_code, 403, refused.data)
        self.assertEqual(refused.data["status"], "expired")
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(allowed.data["status"], "allowed")
