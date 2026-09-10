"""
Buying a package for a television.

A smart TV usually cannot open the captive portal. Many have no portal browser
at all, and the ones that do cannot realistically be typed into with a remote.
So the set never presents a code, which is why the auto_bound displacement
built for exactly this case never fires: CustomerDevice.auto_bound describes
the customer paying on their phone and "the television the code was bought for
was refused 'in use on another device'". The phone that paid holds the place
and the television stays dark.

The portal now lets the buyer type the TV's address on their own phone, and the
voucher binds straight to it. Two things have to hold for that to be safe.

The address must be real. It is read off a settings screen or a sticker and
typed by hand, and normalize_mac deliberately passes an unparseable value
through unchanged -- its docstring explains why, and that reasoning is right on
lookup paths and wrong at a keyboard. Without a check, a dropped digit binds a
device that does not exist: the grant succeeds, the router is configured, the
voucher is spent, and the only symptom is a television that never connects
while its owner is certain they paid.

And the binding must be deliberate, not `auto`. The customer named that device;
it must not be displaced by whatever presents the code next.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Package, RouterDevice, Tenant
from billing.tenancy import tenant_context
from billing.utils import is_real_mac, normalize_mac

TV_MAC = "AA:BB:CC:DD:EE:FF"


class AnAddressTypedByHand(TestCase):
    """is_real_mac exists because normalize_mac promises not to reject."""

    def test_a_real_address_is_accepted_however_it_is_written(self):
        for spelling in ("AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff",
                         "AABBCCDDEEFF", "  aa:bb:cc:dd:ee:ff  "):
            with self.subTest(spelling=spelling):
                self.assertTrue(is_real_mac(spelling))

    def test_a_dropped_digit_is_refused(self):
        """Eleven digits binds a device that does not exist."""
        self.assertFalse(is_real_mac("AA:BB:CC:DD:EE:F"))

    def test_an_extra_digit_is_refused(self):
        self.assertFalse(is_real_mac("AA:BB:CC:DD:EE:FFF"))

    def test_nothing_at_all_is_refused(self):
        for junk in ("", None, "   ", "not a mac", "the one at the back"):
            with self.subTest(junk=junk):
                self.assertFalse(is_real_mac(junk))

    def test_normalize_mac_still_does_not_reject(self):
        """
        The contract this leans on. normalize_mac must keep passing junk
        through, because it runs on lookups where a value we cannot parse has
        to still match itself — so the refusing has to happen separately.
        """
        self.assertEqual(normalize_mac("AA:BB:CC:DD:EE:F"), "AA:BB:CC:DD:EE:F")


class TheVoucherEndpointRefusesAnUntypeableAddress(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="tv-box", ip_address="10.3.0.1",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="24hrs", download_speed=2,
                upload_speed=2, price="40.00", duration_value=24,
                duration_unit="hours", is_hotspot=True, max_devices=2)

    def _validate(self, mac):
        return self.client.post("/api/hotspot/validate/", {
            "code": "ABC123",
            "mac_address": mac,
            "t": self.tenant.public_token,
        }, format="json")

    def test_a_mistyped_tv_address_is_refused_before_anything_is_bound(self):
        r = self._validate("AA:BB:CC:DD:EE")
        self.assertEqual(r.status_code, 400)
        self.assertIn("twelve", r.json()["detail"])

    def test_the_refusal_tells_them_where_to_look(self):
        """
        The customer is holding a remote and looking at the back of a TV. A
        bare "invalid MAC" sends them to call support.
        """
        detail = self._validate("12345").json()["detail"]
        self.assertIn("AA:BB:CC:DD:EE:FF", detail)
        self.assertIn("sticker", detail.lower())

    def test_a_real_address_gets_past_the_shape_check(self):
        """
        It still fails — the code is made up — but it must fail on the CODE,
        not on the address. An unknown voucher is also a 400, so the status
        alone cannot tell those apart; the message can, and the message is
        what the customer reads.
        """
        detail = self._validate(TV_MAC).json().get("detail", "")
        self.assertNotIn(
            "twelve", detail,
            "a valid address was refused as malformed")
        self.assertNotIn("sticker", detail.lower())

    def test_a_missing_address_still_says_so_separately(self):
        r = self.client.post("/api/hotspot/validate/", {
            "code": "ABC123", "t": self.tenant.public_token,
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("required", r.json()["detail"])
