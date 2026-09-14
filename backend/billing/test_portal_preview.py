"""
The operator looking at their own portal from the console.

Until this existed, seeing the portal meant walking to a router, joining the
WiFi on a phone and getting disconnected first — so the things that are easy to
get wrong there were found by customers rather than by the person who can fix
them.

The check that matters most is that the page is INERT. Connect redeems a code
against a MAC and Buy ends in an M-Pesa prompt on a real phone, so an operator
tapping either one to see what it does would spend a stranger's money or burn
their own voucher. The guard lives inside login.html rather than in whatever
serves it, because a guard in the server is one portal redesign away from
being forgotten while the buttons keep working.
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from billing.models import RouterDevice, Tenant
from billing.services import portal_preview
from billing.tenancy import tenant_context


def portal_folder():
    root = Path(settings.BASE_DIR).parent
    for candidate in (Path("/mikrotik-hotspot"), root / "mikrotik-hotspot"):
        if candidate.is_dir():
            return candidate
    return root / "mikrotik-hotspot"


class ThePreviewIsInert(TestCase):
    """
    Read off the portal file itself. These are the lines that stop a preview
    charging somebody, so they are pinned where an edit will trip over them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.login = (portal_folder() / "login.html").read_text(encoding="utf-8")
        cls.config = (portal_folder() / "config.js").read_text(encoding="utf-8")

    def test_the_flag_is_read_from_the_page(self):
        self.assertIn("var PREVIEW = (typeof PORTAL_PREVIEW", self.login)

    def test_connect_is_refused(self):
        """Redeeming a code binds it to a MAC and spends it."""
        self.assertIn("if (previewBlocked(msg)) { return; }", self.login)

    def test_buying_is_refused(self):
        """This call ends in an STK push on somebody's phone."""
        block = self.login.split("function buyPackage(", 1)[1][:600]
        self.assertIn("if (PREVIEW)", block)

    def test_the_refusal_is_before_anything_is_sent(self):
        """
        Guarding after the request has gone is not guarding. The check must
        come before the api() call in each.
        """
        buy = self.login.split("function buyPackage(", 1)[1]
        self.assertLess(buy.index("if (PREVIEW)"), buy.index("api("))

    def test_the_refusal_is_translated(self):
        self.assertIn("t('preview.blocked')", self.login)
        self.assertIn("'preview.blocked'", self.config)
        self.assertIn("Hii ni onyesho tu", self.config)


class RenderingIt(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="prev-r", ip_address="10.9.0.120",
                username="u", password="p", is_active=True)

    def render(self):
        return portal_preview.render(self.tenant, "https://api.example.com/api")

    # ───────────────────────────────────────────────── the router's variables

    def test_no_mikrotik_variable_survives(self):
        """
        RouterOS substitutes these before serving. Nothing does that here, so
        any left behind would render as literal text on the operator's screen.
        """
        html = self.render()

        for token in ("$(mac)", "$(chap-id)", "$(chap-challenge)",
                      "$(link-login-only)", "$(link-orig)", "$(if error)",
                      "$(endif)"):
            self.assertNotIn(token, html, f"{token} left in the preview")

    def test_the_error_block_is_dropped_whole(self):
        """
        Not just its variable. Leaving the markup would show an empty red box
        that the operator would reasonably ask about.

        The MARKUP, not the name: .router-error is also a CSS rule, and that
        rule is supposed to survive — the preview serves the same stylesheet
        the routers do, and a page that styles nothing it renders is still the
        page customers get.
        """
        html = self.render()

        self.assertNotIn('<div class="router-error">', html)
        self.assertIn(".router-error {", html)

    def test_the_flag_is_set(self):
        self.assertIn("var PORTAL_PREVIEW = true;", self.render())

    # ─────────────────────────────────────────────────────────── self-contained

    def test_the_scripts_are_inlined(self):
        """
        One endpoint serves this. A browser asking for config.js beside it
        would get the API's 404 rather than a script, and the page would not
        run at all.
        """
        html = self.render()

        self.assertNotIn('<script src="config.js">', html)
        self.assertNotIn('<script src="md5.js">', html)
        self.assertIn("var API_BASE", html)
        self.assertIn("var TENANT_TOKEN", html)

    def test_the_operators_own_token_is_used(self):
        self.assertIn(self.tenant.public_token, self.render())

    def test_the_api_base_is_the_one_asked_for(self):
        """
        Built from the request rather than a setting, so a preview opened
        against staging talks to staging.
        """
        self.assertIn("https://api.example.com/api", self.render())

    def test_no_placeholder_survives(self):
        html = self.render()

        for placeholder in ("YOUR-OPERATOR-TOKEN", "YOUR-ROUTER-TOKEN",
                            "https://your-backend.com/api"):
            self.assertNotIn(placeholder, html)

    def test_the_flag_comes_after_the_config(self):
        """A config that happened to define it must not win."""
        html = self.render()

        self.assertLess(html.index("var TENANT_TOKEN"),
                        html.index("var PORTAL_PREVIEW"))

    def test_an_operator_with_no_router_is_told_why(self):
        """
        There is no router token to build config.js with, and an empty token
        is the state portal_files refuses to upload. Better to say so than to
        render a portal that identifies itself to nobody.
        """
        with tenant_context(self.tenant):
            RouterDevice.objects.filter(pk=self.router.pk).update(
                is_active=False)

        with self.assertRaises(ValueError):
            self.render()


class TheEndpoint(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            RouterDevice.objects.create(
                tenant=self.tenant, name="prev-e", ip_address="10.9.0.121",
                username="u", password="p", is_active=True)

    def get(self, **params):
        return self.client.get("/api/hotspot/portal-preview/", params)

    def test_it_serves_the_portal(self):
        resp = self.get(t=self.tenant.public_token)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp["Content-Type"])
        self.assertIn(b"PORTAL_PREVIEW", resp.content)

    def test_an_unknown_token_is_refused(self):
        self.assertEqual(self.get(t="nonsense").status_code, 404)

    @override_settings(CORS_ALLOWED_ORIGINS=["https://app.example.com"])
    def test_only_the_console_may_frame_it(self):
        """
        The page carries the operator's token and is a working portal. Framed
        anywhere else it would be a portal on somebody else's site.
        """
        resp = self.get(t=self.tenant.public_token)

        self.assertIn("frame-ancestors", resp["Content-Security-Policy"])
        self.assertIn("https://app.example.com",
                      resp["Content-Security-Policy"])

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_with_no_origins_configured_it_closes_rather_than_opens(self):
        """
        A preview nobody can frame is a bug somebody reports. One anybody can
        frame is not.
        """
        resp = self.get(t=self.tenant.public_token)

        self.assertIn("'self'", resp["Content-Security-Policy"])

    def test_it_is_not_cached_or_indexed(self):
        resp = self.get(t=self.tenant.public_token)

        self.assertEqual(resp["Cache-Control"], "no-store")
        self.assertIn("noindex", resp["X-Robots-Tag"])
