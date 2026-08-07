"""
Who is allowed to deliver an M-Pesa result, and what happens when we say no.

The address list was the first and only thing this endpoint checked. It held
five entries against a set Safaricom publishes more of and rotates without
notice, and a miss produced the worst outcome the system can: the customer is
charged, the callback is refused, no access is granted, and nothing anywhere
records that it happened.

Two things are asserted here. That a genuine callback from an address nobody
listed still gets through — on evidence, not on trust. And that a refusal is
never silent again.
"""

from decimal import Decimal
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import (
    Customer, Invoice, Package, RouterDevice, Subscription, Tenant,
)
from billing.tenancy import tenant_context

SAFARICOM = "196.201.214.200"
UNLISTED = "196.201.212.127"      # real Safaricom range, absent from our list
STRANGER = "203.0.113.9"


def stk_success(invoice_number, checkout="ws_CO_1"):
    return {
        "Body": {"stkCallback": {
            "MerchantRequestID": "m-1",
            "CheckoutRequestID": checkout,
            "ResultCode": 0,
            "ResultDesc": "The service request is processed successfully.",
            "CallbackMetadata": {"Item": [
                {"Name": "Amount", "Value": 50.0},
                {"Name": "MpesaReceiptNumber", "Value": "TEST12345"},
                {"Name": "PhoneNumber", "Value": 254700000001},
                {"Name": "AccountReference", "Value": invoice_number},
            ]},
        }}
    }


@override_settings(MPESA_TRUSTED_IPS=[SAFARICOM], MPESA_ALLOW_LOCAL_CALLBACK=False)
class MpesaCallbackSourceTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            router = RouterDevice.objects.create(
                tenant=self.tenant, name="r", ip_address="10.0.0.1",
                username="a", password="p")
            package = Package.objects.create(
                tenant=self.tenant, name="1hr", download_speed=5, upload_speed=2,
                price=Decimal("50.00"), duration_value=1, duration_unit="hours",
                monthly_data_cap_gb=0, is_hotspot=True)
            customer = Customer.objects.create(
                tenant=self.tenant, full_name="Asha", phone="254700000001",
                connection_type="hotspot", router=router)
            subscription = Subscription.objects.create(
                tenant=self.tenant, customer=customer, package=package,
                status="active", expiry_date=timezone.now() + timedelta(hours=1))
            # Subscription.save() raises the invoice itself, and
            # Invoice.subscription is one-to-one — so this takes the one that
            # already exists rather than making a second, which is also what
            # the real flow does before a push.
            self.invoice = Invoice.objects.get(subscription=subscription)
            self.invoice.payment_status = "pending"
            self.invoice.save(update_fields=["payment_status"])
        self.url = f"/api/mpesa/callback/{self.tenant.public_token}/"

    def _post(self, payload, ip):
        return self.client.post(
            self.url, payload, content_type="application/json", REMOTE_ADDR=ip)

    def test_a_listed_address_is_accepted(self):
        resp = self._post(stk_success(self.invoice.invoice_number), SAFARICOM)
        self.assertNotEqual(resp.status_code, 403)

    def test_an_unlisted_safaricom_address_still_gets_through(self):
        """
        The regression this file exists for. Safaricom rotates its callback
        addresses; our list cannot be assumed complete. A push we initiated,
        arriving with the invoice number we generated, is genuine no matter
        which address it came from — and the customer has already paid.
        """
        resp = self._post(stk_success(self.invoice.invoice_number), UNLISTED)
        self.assertNotEqual(
            resp.status_code, 403,
            "a real payment was refused because the address was not on a list")

    def test_a_stranger_who_knows_the_token_is_still_refused(self):
        """
        The URL token is not a secret — it ships in config.js on every router
        and is served to every subscriber's browser. Knowing it must not be
        enough to claim a payment happened.
        """
        resp = self._post(stk_success("INV-DOES-NOT-EXIST"), STRANGER)
        self.assertEqual(resp.status_code, 403)

    def test_a_stranger_replaying_a_real_invoice_number_from_a_bad_address(self):
        """
        Correlation is evidence, not proof. Someone who has both the token and
        a real invoice number gets through — and that is the deliberate limit
        of this check, which is why the acceptance is logged rather than
        silent. The amount and receipt are still validated downstream.
        """
        resp = self._post(stk_success(self.invoice.invoice_number), STRANGER)
        self.assertNotEqual(resp.status_code, 403)

    def test_a_refusal_is_never_silent(self):
        """
        The whole point. Before this, a refused callback left a 403 in the
        access log and nothing else — a charged customer, no access, and
        nothing to diagnose it from.
        """
        with self.assertLogs("billing.views", level="WARNING") as captured:
            self._post(stk_success("INV-DOES-NOT-EXIST"), STRANGER)

        blob = "\n".join(captured.output)
        self.assertIn("REFUSED", blob)
        self.assertIn(STRANGER, blob)
        self.assertIn("charged", blob, "the log should say what it costs")

    def test_an_acceptance_from_an_unlisted_address_says_so(self):
        """An operator seeing this repeatedly should widen the list."""
        with self.assertLogs("billing.views", level="WARNING") as captured:
            self._post(stk_success(self.invoice.invoice_number), UNLISTED)

        blob = "\n".join(captured.output)
        self.assertIn(UNLISTED, blob)
        self.assertIn("MPESA_TRUSTED_IPS", blob)

    def test_an_unknown_tenant_token_is_still_rejected(self):
        resp = self.client.post(
            "/api/mpesa/callback/not-a-real-token/",
            stk_success(self.invoice.invoice_number),
            content_type="application/json", REMOTE_ADDR=SAFARICOM)
        self.assertEqual(resp.status_code, 404)

    def test_a_failed_push_from_an_unlisted_address_is_refused(self):
        """
        No metadata, so nothing to correlate — and nothing at stake either:
        ResultCode != 0 means no money moved. Refused, and logged.
        """
        payload = {"Body": {"stkCallback": {
            "MerchantRequestID": "m-2", "CheckoutRequestID": "ws_CO_2",
            "ResultCode": 1032, "ResultDesc": "Request cancelled by user",
        }}}
        with self.assertLogs("billing.views", level="WARNING"):
            resp = self._post(payload, UNLISTED)
        self.assertEqual(resp.status_code, 403)
