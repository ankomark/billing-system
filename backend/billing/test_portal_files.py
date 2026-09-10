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


class LineEndingsAreNotADifference(TestCase):
    """
    Every portal file on the routers was reported as drifted, for ever,
    because a copy uploaded from a Windows checkout has CRLF and the server's
    checkout has LF. Content was identical in all four cases -- confirmed by
    fetching them -- and browsers do not care. A status command whose output
    you learn to ignore is worse than no status command.
    """

    def test_crlf_and_lf_are_the_same_file(self):
        self.assertTrue(portal_files.same_content(b"a\r\nb\r\n", b"a\nb\n"))

    def test_a_real_change_is_still_a_change(self):
        self.assertFalse(portal_files.same_content(b"a\r\nb\r\n", b"a\nc\n"))

    def test_both_byte_counts_are_accepted(self):
        """
        compare() reads sizes over the API and cannot normalise, so it has to
        know that the same text is one byte per line longer as CRLF.
        """
        allowed, lf = portal_files.sizes_that_mean_unchanged(b"a\nb\nc\n")
        self.assertEqual(lf, 6)
        self.assertEqual(allowed, {6, 9})

    def test_the_real_md5_js_case(self):
        """
        172 lines, 8864 bytes as LF, 9036 on the router. Reported as drift on
        both routers until this.
        """
        raw = b"x\n" * 172
        allowed, lf = portal_files.sizes_that_mean_unchanged(raw)
        self.assertEqual(lf, 344)
        self.assertIn(344 + 172, allowed)

    def test_a_file_with_no_newlines_has_one_answer(self):
        allowed, _ = portal_files.sizes_that_mean_unchanged(b"binary-ish")
        self.assertEqual(len(allowed), 1)

    def test_starting_from_crlf_gives_the_same_pair(self):
        """The local checkout may itself be CRLF; the answer must not change."""
        self.assertEqual(portal_files.sizes_that_mean_unchanged(b"a\r\nb\r\n"),
                         portal_files.sizes_that_mean_unchanged(b"a\nb\n"))
