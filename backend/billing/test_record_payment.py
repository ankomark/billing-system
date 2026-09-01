"""
Taking money for a bill that already exists.

A hotspot walk-up pays through the portal and the M-Pesa callback settles their
invoice. A PPPoE line is the other way round: created first, paid afterwards —
and nothing in the platform could take that payment. The interface offered
"give free access", which writes the sale off, or creating the customer again
with a package, which duplicates them. An operator holding a real 2,500/- had
no honest way to say so, and the first PPPoE subscriber was connected by
comping him: a sale recorded as a giveaway.

There is deliberately no "mark as paid". A paid invoice is a consequence of a
payment, not a state to set — Payment.save() settles the invoice, activates the
subscription, assigns a router, mints the voucher for a hotspot subscriber and
provisions the hardware. A button that flipped the flag would tidy the books
and provision nothing: the customer stays refused by the router while every
record says they are fine. That is the failure this endpoint exists to avoid,
so the tests below check the cascade rather than the flag.
"""

from decimal import Decimal


from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from billing.models import (
    Customer, Invoice, Package, Payment, RouterDevice, Subscription, Tenant,
    User,
)
from billing.tenancy import tenant_context


class RecordPaymentTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.admin = User.objects.create_user(
                username="boss", password="pw", role="tenant_admin",
                tenant=self.tenant)
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.9",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="home 10mbps", download_speed=10,
                upload_speed=10, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", monthly_data_cap_gb=0,
                is_hotspot=False, max_devices=4)
            self.customer = Customer.objects.create(
                tenant=self.tenant, full_name="Enock", phone="254700111222",
                connection_type="pppoe", router=self.router,
                pppoe_username="enock", pppoe_password="pw")
            self.subscription = Subscription.objects.create(
                tenant=self.tenant, customer=self.customer,
                package=self.package)

        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = reverse("customer-record-payment",
                           args=[self.customer.id])

    def _post(self, **body):
        # Nothing is patched, and nothing needs to be. Payment.save() hands
        # provisioning to transaction.on_commit, which TestCase never runs
        # because the whole test is inside a transaction that is rolled back.
        # The customer already has a router, so the one synchronous router call
        # in that path — picking one for a subscriber who has none — is skipped
        # too. What runs here is exactly the bookkeeping these tests are about.
        with tenant_context(self.tenant):
            return self.client.post(self.url, body, format="json")

    def _invoice(self):
        return Invoice.objects.all_tenants().get(
            subscription=self.subscription)

    # ---- the thing it is for -------------------------------------------

    def test_it_settles_the_bill_that_was_already_there(self):
        """
        The difference from comping. Comp creates a second subscription and
        leaves the unpaid one behind for ever; this settles the one in hand.
        """
        self.assertEqual(self._invoice().payment_status, "unpaid")

        r = self._post(method="cash")
        self.assertEqual(r.status_code, 201, r.data)

        self.assertEqual(self._invoice().payment_status, "paid")
        self.assertEqual(
            Subscription.objects.all_tenants().filter(
                customer=self.customer).count(),
            1, "a second subscription was created instead of settling the first")

    def test_it_records_a_payment_rather_than_flipping_a_flag(self):
        """
        Payment.save() is the machinery that provisions. A flag flipped without
        one settles the books and leaves the customer refused by the router.
        """
        self._post(method="cash", reference="till 12345")

        payment = Payment.objects.all_tenants().get(
            subscription=self.subscription)
        self.assertEqual(payment.amount, Decimal("2500.00"))
        self.assertEqual(payment.method, "cash")
        self.assertEqual(payment.reference, "till 12345")

    def test_the_amount_defaults_to_the_bill(self):
        self._post(method="mpesa")
        payment = Payment.objects.all_tenants().get(
            subscription=self.subscription)
        self.assertEqual(payment.amount, self._invoice().total_amount)

    def test_a_part_payment_is_recorded_as_what_was_given(self):
        """
        Operators discount. The books must say what was received, not what was
        asked for.
        """
        self._post(method="cash", amount="2000")
        payment = Payment.objects.all_tenants().get(
            subscription=self.subscription)
        self.assertEqual(payment.amount, Decimal("2000"))

    # ---- what it must refuse -------------------------------------------

    def test_the_same_bill_cannot_be_paid_twice(self):
        """
        Revenue counts Payment rows, so a double-tap is money the business
        never received.
        """
        self.assertEqual(self._post(method="cash").status_code, 201)
        # Naming the subscription, which is what the interface does — a
        # double-tap sends the same id again. Without one the endpoint looks
        # for anything outstanding and correctly finds nothing.
        again = self._post(method="cash", subscription_id=self.subscription.id)
        self.assertEqual(again.status_code, 409, again.data)
        self.assertEqual(
            Payment.objects.all_tenants().filter(
                subscription=self.subscription).count(), 1)

    def test_zero_is_sent_to_the_giveaway_path_instead(self):
        """Free access has to say why, and must not read as a sale of nothing."""
        r = self._post(method="cash", amount="0")
        self.assertEqual(r.status_code, 400)
        self.assertIn("free access", r.data["detail"].lower())

    def test_a_negative_amount_is_refused(self):
        self.assertEqual(self._post(method="cash", amount="-100").status_code, 400)

    def test_an_amount_that_is_not_a_number_is_refused(self):
        self.assertEqual(self._post(method="cash", amount="lots").status_code, 400)

    def test_the_method_has_to_be_one_we_take(self):
        self.assertEqual(self._post(method="").status_code, 400)
        self.assertEqual(self._post(method="barter").status_code, 400)

    def test_comp_is_not_a_payment_method_here(self):
        """
        It has its own endpoint, which demands a reason. Accepting it here
        would let a giveaway be booked as a sale.
        """
        self.assertEqual(self._post(method="comp").status_code, 400)

    def test_another_customers_subscription_cannot_be_settled(self):
        """
        Scoped to the customer in the URL. Looked up globally, an id from
        another account would settle a stranger's invoice and provision them.
        """
        with tenant_context(self.tenant):
            other = Customer.objects.create(
                tenant=self.tenant, full_name="Someone Else",
                phone="254700333444", connection_type="pppoe",
                router=self.router, pppoe_username="other",
                pppoe_password="pw")
            theirs = Subscription.objects.create(
                tenant=self.tenant, customer=other, package=self.package)

        r = self._post(method="cash", subscription_id=theirs.id)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(
            Invoice.objects.all_tenants().get(
                subscription=theirs).payment_status,
            "unpaid", "a stranger's invoice was settled")

    def test_a_customer_with_nothing_outstanding_is_told_so(self):
        self._post(method="cash")
        r = self._post(method="cash")
        self.assertIn(r.status_code, (400, 409))

    def test_staff_may_not_take_payments(self):
        """
        Money belongs to whoever answers for it, which is the same rule
        comping follows.
        """
        with tenant_context(self.tenant):
            staff = User.objects.create_user(
                username="counter", password="pw", role="tenant_staff",
                tenant=self.tenant)
        client = APIClient()
        client.force_authenticate(staff)
        r = client.post(self.url, {"method": "cash"}, format="json")
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(self._invoice().payment_status, "unpaid")
