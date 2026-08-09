"""
PayBill and Buy Goods are different products, and the push has to say which.

The STK payload hardcoded CustomerPayBillOnline for every operator. Against a
Buy Goods till Safaricom accepts the request — ResponseCode 0, "Request
accepted for processing" — and then fails it a second later with ResultCode
2029, "Failed due to an unresolved reason type", having delivered no prompt to
the customer's phone. Nothing in that response names the field that was wrong,
and the operator's own credentials test passes, because OAuth is fine.

Found on a real till after three live attempts.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from billing.mpesa_client import shortcode_config, generate_password
from billing.models import SystemSetting, Tenant
from billing.tenancy import tenant_context


class ShortcodeConfigTests(TestCase):

    def setUp(self):
        # get_setting caches, and the cache is not rolled back with the rows.
        # Without this, a till and a store number set by one test are still
        # being read by the next, and the test asserting that PayBill is the
        # default fails against a value it never set.
        from billing.config import clear_settings_cache
        clear_settings_cache()
        self.tenant = Tenant.objects.get(slug="skylink")

    def _set(self, **pairs):
        with tenant_context(self.tenant):
            for key, value in pairs.items():
                SystemSetting.objects.update_or_create(
                    tenant=self.tenant, key=key, defaults={"value": value})
            from billing.config import clear_settings_cache
            clear_settings_cache()

    def test_paybill_is_the_default_when_nothing_says_otherwise(self):
        """Operators created before this existed must keep working."""
        self._set(MPESA_SHORTCODE="400200")
        with tenant_context(self.tenant):
            cfg = shortcode_config(tenant=self.tenant)
        self.assertEqual(cfg["transaction_type"], "CustomerPayBillOnline")
        self.assertEqual(cfg["business_shortcode"], "400200")
        self.assertEqual(cfg["party_b"], "400200")

    def test_a_till_uses_buy_goods(self):
        self._set(MPESA_SHORTCODE="3439137", MPESA_SHORTCODE_TYPE="till")
        with tenant_context(self.tenant):
            cfg = shortcode_config(tenant=self.tenant)
        self.assertEqual(cfg["transaction_type"], "CustomerBuyGoodsOnline")

    def test_a_till_without_a_store_number_signs_with_the_till(self):
        """Most tills are issued with the store number equal to the till."""
        self._set(MPESA_SHORTCODE="3439137", MPESA_SHORTCODE_TYPE="till",
                  MPESA_STORE_NUMBER="")
        with tenant_context(self.tenant):
            cfg = shortcode_config(tenant=self.tenant)
        self.assertEqual(cfg["business_shortcode"], "3439137")
        self.assertEqual(cfg["party_b"], "3439137")

    def test_a_distinct_store_number_signs_and_the_till_is_paid(self):
        """
        The two numbers do different jobs: the store identifies the merchant
        and signs the password, the till is where the money lands.
        """
        self._set(MPESA_SHORTCODE="3439137", MPESA_SHORTCODE_TYPE="till",
                  MPESA_STORE_NUMBER="123456")
        with tenant_context(self.tenant):
            cfg = shortcode_config(tenant=self.tenant)
        self.assertEqual(cfg["business_shortcode"], "123456")
        self.assertEqual(cfg["party_b"], "3439137")

    def test_the_password_is_signed_with_the_store_not_the_till(self):
        """Signing with the till gives a password Safaricom rejects."""
        self._set(MPESA_SHORTCODE="3439137", MPESA_SHORTCODE_TYPE="till",
                  MPESA_STORE_NUMBER="123456", MPESA_PASSKEY="k" * 64)
        with tenant_context(self.tenant):
            encoded, timestamp = generate_password(tenant=self.tenant)

        import base64
        decoded = base64.b64decode(encoded).decode()
        self.assertTrue(decoded.startswith("123456"), decoded[:20])
        self.assertFalse(decoded.startswith("3439137"))

    def test_case_and_whitespace_do_not_decide_how_someone_gets_paid(self):
        self._set(MPESA_SHORTCODE="3439137", MPESA_SHORTCODE_TYPE="  TILL ")
        with tenant_context(self.tenant):
            cfg = shortcode_config(tenant=self.tenant)
        self.assertEqual(cfg["transaction_type"], "CustomerBuyGoodsOnline")


@override_settings(PLATFORM_BASE_URL="https://billing.example.com")
class StkPayloadTests(TestCase):
    """
    What actually goes on the wire.

    PLATFORM_BASE_URL is overridden because initiate_stk_push derives the
    callback URL before it builds the payload, and there is no default for it —
    it is not in .env.example, because the only correct value is a hostname the
    operator has and we do not. Without this the push raises
    PaymentsNotConfigured and nothing below ever runs: the test failed on the
    line before the thing it was written to check.
    """

    def setUp(self):
        from billing.config import clear_settings_cache
        clear_settings_cache()
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            # Explicitly blank, not merely unset: a store number left over in
            # the cache from another test would otherwise sign this push.
            SystemSetting.objects.update_or_create(
                tenant=self.tenant, key="MPESA_STORE_NUMBER",
                defaults={"value": ""})
            for key, value in {
                "MPESA_ENV": "production",
                "MPESA_CONSUMER_KEY": "k",
                "MPESA_CONSUMER_SECRET": "s",
                "MPESA_PASSKEY": "p" * 64,
                "MPESA_SHORTCODE": "3439137",
                "MPESA_SHORTCODE_TYPE": "till",
            }.items():
                SystemSetting.objects.update_or_create(
                    tenant=self.tenant, key=key, defaults={"value": value})
            from billing.config import clear_settings_cache
            clear_settings_cache()

    def test_a_till_push_names_buy_goods_on_the_wire(self):
        from billing import mpesa_client

        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_1"}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(json)
            return FakeResponse()

        with patch.object(mpesa_client, "get_mpesa_access_token", return_value="tok"), \
             patch.object(mpesa_client.requests, "post", fake_post), \
             tenant_context(self.tenant):
            mpesa_client.initiate_stk_push(
                phone_number="254700000001", amount=Decimal("50"),
                account_reference="INV-1", description="test",
                tenant=self.tenant)

        self.assertEqual(captured["TransactionType"], "CustomerBuyGoodsOnline")
        self.assertEqual(captured["PartyB"], "3439137")
        self.assertEqual(captured["BusinessShortCode"], "3439137")

    def test_the_callback_url_carries_this_operators_token(self):
        """
        Reachable now that the push runs to the end, and worth asserting: the
        token in that URL is the only thing telling the callback whose
        credentials to load. Derived wrong, every operator's results would be
        posted to a URL that resolves to somebody else — or to none of them.
        """
        from billing import mpesa_client

        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_1"}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured.update(json)
            return FakeResponse()

        with patch.object(mpesa_client, "get_mpesa_access_token", return_value="tok"), \
             patch.object(mpesa_client.requests, "post", fake_post), \
             tenant_context(self.tenant):
            mpesa_client.initiate_stk_push(
                phone_number="254700000001", amount=Decimal("50"),
                account_reference="INV-1", description="test",
                tenant=self.tenant)

        self.assertEqual(
            captured["CallBackURL"],
            f"https://billing.example.com/api/mpesa/callback/"
            f"{self.tenant.public_token}/")
