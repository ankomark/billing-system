"""
The portal file's half of the featured package.

login.html is uploaded to a router by hand and imported by nothing, so a
mistake in it does not fail a build — it renders as a broken page in front of
somebody standing at a counter. MikrotikPortalPageTests exists for that reason;
these are the checks specific to the featured card and the two-up grid that
replaced the single column.

The rule that matters most: the portal must not trust the id it is given. The
backend validates it against the list, and the portal validates it again
against the same list — because a router carrying an older copy of this file,
or a backend that predates the setting, has to degrade to a plain list rather
than to an empty card.
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase


def portal_folder():
    """Beside the backend in a checkout, bind-mounted at the root in Docker."""
    root = Path(settings.BASE_DIR).parent
    for candidate in (root / "mikrotik-hotspot", Path("/mikrotik-hotspot")):
        if candidate.is_dir():
            return candidate
    return root / "mikrotik-hotspot"


class TheFeaturedCard(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.login = (portal_folder() / "login.html").read_text(encoding="utf-8")
        cls.config = (portal_folder() / "config.js").read_text(encoding="utf-8")

    # ───────────────────────────────────────────────────────────── it is read

    def test_the_portal_reads_the_setting(self):
        self.assertIn("data.featured_package", self.login)

    def test_it_is_passed_to_the_renderer(self):
        self.assertIn("function renderPackages(list, featuredId)", self.login)

    def test_the_id_is_matched_against_the_list(self):
        """
        Not trusted. The backend checks it too, but a router running an older
        copy of this file must still degrade to a plain list rather than
        rendering a card for a package that is not there.
        """
        self.assertIn("list[f].id === featuredId", self.login)

    def test_a_missing_value_is_handled(self):
        """
        A backend that predates the setting sends no key at all, and older
        routers will be talking to it for a while.
        """
        self.assertIn(
            "featuredId !== null && featuredId !== undefined", self.login)

    # ─────────────────────────────────────────────────────────── it is drawn

    def test_the_featured_card_has_its_own_class(self):
        self.assertIn("pkg pkg--featured", self.login)
        self.assertIn(".pkg--featured", self.login)

    def test_the_rest_go_in_a_grid(self):
        self.assertIn("pkg-grid", self.login)
        self.assertIn("grid-template-columns: 1fr 1fr", self.login)

    def test_an_opened_package_takes_the_whole_row(self):
        """
        The phone field and the television box need the width. A pay panel
        squeezed into half of a two-up grid is where somebody mistypes their
        number.
        """
        self.assertIn(".pkg.open { grid-column: 1 / -1", self.login)

    def test_the_featured_one_is_not_repeated_below(self):
        self.assertIn("if (list[i] === featured) { continue; }", self.login)

    # ──────────────────────────────────────────────────────── operator input

    def test_the_flag_is_translated_not_hardcoded(self):
        self.assertIn("t('pkg.featured')", self.login)

    def test_both_languages_have_the_flag(self):
        self.assertIn("'pkg.featured':     'Featured'", self.config)
        self.assertIn("'pkg.featured':     'Maalum'", self.config)

    def test_the_package_name_is_still_written_as_text(self):
        """
        A package name is operator input and this page has no framework
        escaping anything. It must never be interpolated into innerHTML.
        """
        self.assertIn(".pkg-name').textContent = pkg.name", self.login)

    def test_the_flag_is_written_as_text_too(self):
        self.assertIn(".pkg-flag').textContent", self.login)

    # ───────────────────────────────────────────────────────────── the skin

    def test_the_page_is_black(self):
        self.assertIn("background: #000;", self.login)

    def test_no_gradient_survives_on_a_card(self):
        """
        Every gradient was removed deliberately. A wash behind a price makes
        the price harder to read, and on a flat dark page it buys nothing.
        """
        for marker in (".pkg {", ".pkg--featured {", ".reconnect {"):
            block = self.login.split(marker, 1)[1].split("}", 1)[0]
            self.assertNotIn("gradient", block, f"gradient inside {marker}")

    def test_the_actions_share_one_green(self):
        for rule in (".pkg-buy", ".rc-row button", ".brand-icon"):
            block = self.login.split(rule, 1)[1].split("}", 1)[0]
            self.assertIn("#056349", block, f"{rule} is not the action green")

    def test_the_featured_button_is_the_bright_one(self):
        block = self.login.split(".pkg--featured .pkg-buy", 1)[1].split("}", 1)[0]
        self.assertIn("#00e5a8", block)

    def test_the_notice_keeps_its_amber(self):
        """
        The one element whose job is to interrupt, so it does not take the
        page's colour.
        """
        block = self.login.split(".notice {", 1)[1].split("}", 1)[0]
        self.assertIn("255,176,32", block)

    def test_prices_line_up(self):
        block = self.login.split(".pkg-price {", 1)[1].split("}", 1)[0]
        self.assertIn("tabular-nums", block)
