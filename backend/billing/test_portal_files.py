"""
Uploading the captive-portal files without WinBox.

Two things carry the risk, and both are tested here rather than trusted.

config.js is per-router: it carries the API address, the operator's token and
that router's token. Uploading the repository copy unchanged would leave the
portal identifying itself to nobody, and _portal_router would fall back to
selection by load -- the exact bug the router token exists to prevent. So the
build refuses if any placeholder survives substitution.

And the replacement has to be proved a superset of what is already there. The
first attempt at this compared the two files with a hand-rolled filter that
also stripped continuation lines from the original, so a perfectly correct
substitution was refused and nothing was uploaded. The check is exact now:
does the router's copy hold any line the rebuild does not?
"""

from django.test import TestCase

from billing.models import RouterDevice, Tenant
from billing.services import portal_files
from billing.tenancy import tenant_context

TEMPLATE = """\
var API_BASE = 'https://your-backend.com/api';
var TENANT_TOKEN = 'YOUR-OPERATOR-TOKEN';
var ROUTER_TOKEN = 'YOUR-ROUTER-TOKEN';
var STRINGS = { en: { 'buy': 'Buy' } };
"""


class BuildingConfigForOneRouter(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="box", ip_address="10.2.0.1",
                username="u", password="p")
        self.router.refresh_from_db()

    def build(self):
        return portal_files.build_config(
            self.router, "https://api.example.com/api", template=TEMPLATE)

    def test_every_placeholder_is_replaced(self):
        built = self.build()
        for placeholder in portal_files.PLACEHOLDERS:
            self.assertNotIn(placeholder, built)

    def test_the_router_identifies_itself(self):
        """
        Without its own token the portal cannot say which box a walk-up is
        standing at, and selection falls back to load — which on a hotspot
        estate is a tie at zero and an arbitrary winner.
        """
        self.assertIn(self.router.public_token, self.build())

    def test_the_operator_and_api_reach_the_file(self):
        built = self.build()
        self.assertIn(self.tenant.public_token, built)
        self.assertIn("https://api.example.com/api", built)

    def test_a_router_with_no_token_is_refused_not_uploaded(self):
        """
        A config.js still carrying YOUR-ROUTER-TOKEN is worse than an old one:
        it looks deployed and identifies nothing.
        """
        self.router.public_token = ""
        with self.assertRaises(ValueError):
            portal_files.build_config(
                self.router, "https://api.example.com/api", template=TEMPLATE)

    def test_nothing_else_in_the_file_is_touched(self):
        self.assertIn("var STRINGS = { en: { 'buy': 'Buy' } };", self.build())


class ProvingTheReplacementLosesNothing(TestCase):

    def test_added_lines_alone_are_safe(self):
        old = "a\nb\nc\n"
        new = "a\nb\nEXTRA\nc\n"
        self.assertEqual(portal_files.lost_lines(old, new), [])

    def test_a_line_only_on_the_router_is_reported(self):
        old = "a\nHAND EDITED\nb\n"
        new = "a\nb\n"
        self.assertEqual(portal_files.lost_lines(old, new), ["HAND EDITED"])

    def test_identical_files_lose_nothing(self):
        self.assertEqual(portal_files.lost_lines("a\nb\n", "a\nb\n"), [])

    def test_line_endings_do_not_count_as_a_difference(self):
        """
        The router's copy comes back with whatever endings it was uploaded
        with. Treating CRLF as a changed line would refuse every upload.
        """
        self.assertEqual(portal_files.lost_lines("a\r\nb\r\n", "a\nb\n"), [])

    def test_a_changed_line_is_reported(self):
        """
        A substitution that alters an existing line rather than adding one is
        exactly the case that must not pass silently.
        """
        old = "var API_BASE = 'https://old.example.com/api';\n"
        new = "var API_BASE = 'https://new.example.com/api';\n"
        self.assertEqual(len(portal_files.lost_lines(old, new)), 1)

    def test_an_empty_router_copy_loses_nothing(self):
        """A file that is not on the router yet cannot lose anything."""
        self.assertEqual(portal_files.lost_lines("", "a\nb\n"), [])
