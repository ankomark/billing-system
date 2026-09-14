"""
Which package the portal puts at the top, and why it is a setting.

login.html is uploaded to each MikroTik by hand. A featured package written
into that file costs a site visit to change and another to change back, so an
operator who wants the 3hr bundle featured over a weekend cannot do it at all.
The notice already solved this problem and recorded the reasoning; this follows
it exactly — a setting, served with the package list, reaching every router at
once.

The part worth testing hardest is what happens when the setting is stale. It is
a number typed once and then left alone, while the package it names can be
archived, re-priced or deleted months later. So it is validated against the
list actually being sent rather than trusted, and anything that no longer
matches degrades to a plain list instead of an empty card or a stale price.
"""

from decimal import Decimal

from django.test import TestCase

from billing.models import Package, SystemSetting, Tenant
from billing.tenancy import tenant_context


class FeaturedPackage(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.cheap = Package.objects.create(
                tenant=self.tenant, name="3hrs - 3GB", download_speed=4,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", data_cap_mb=3000, is_hotspot=True)
            self.day = Package.objects.create(
                tenant=self.tenant, name="24hrs - 10GB", download_speed=4,
                upload_speed=2, price=Decimal("40.00"), duration_value=24,
                duration_unit="hours", data_cap_mb=10000, is_hotspot=True)

    def _set(self, value):
        SystemSetting.objects.update_or_create(
            tenant=self.tenant, key="HOTSPOT_FEATURED_PACKAGE",
            defaults={"value": value})
        from billing.config import clear_settings_cache
        clear_settings_cache(tenant=self.tenant)

    def _get(self):
        return self.client.get(
            "/api/hotspot/packages/", {"t": self.tenant.public_token})

    # ─────────────────────────────────────────────────────── the ordinary case

    def test_the_chosen_package_is_named(self):
        self._set(str(self.cheap.id))

        self.assertEqual(self._get().json()["featured_package"], self.cheap.id)

    def test_flipping_it_reaches_the_portal(self):
        """
        The whole point. An operator moves the weekend offer from one package
        to another and every router follows, with nobody visiting a site.
        """
        self._set(str(self.cheap.id))
        self.assertEqual(self._get().json()["featured_package"], self.cheap.id)

        self._set(str(self.day.id))
        self.assertEqual(self._get().json()["featured_package"], self.day.id)

    def test_clearing_it_leaves_a_plain_list(self):
        self._set(str(self.cheap.id))
        self._set("")

        body = self._get().json()
        self.assertIsNone(body["featured_package"])
        self.assertTrue(body["results"])

    def test_unset_is_null_not_missing(self):
        """
        The portal reads one key either way. A missing key and a null are the
        same thing to it, but only one of them is the same thing to an older
        portal that has not been re-uploaded.
        """
        body = self._get().json()

        self.assertIn("featured_package", body)
        self.assertIsNone(body["featured_package"])

    # ──────────────────────────────────────────────────────── when it is stale

    def test_an_archived_package_stops_being_featured(self):
        """
        The setting is a number typed once; the package can be retired months
        later. Featuring one that is no longer on sale would put a price in
        front of a subscriber that they cannot buy.
        """
        self._set(str(self.cheap.id))
        with tenant_context(self.tenant):
            Package.objects.filter(pk=self.cheap.id).update(is_archived=True)

        self.assertIsNone(self._get().json()["featured_package"])

    def test_a_deleted_package_stops_being_featured(self):
        self._set(str(self.cheap.id))
        with tenant_context(self.tenant):
            Package.objects.filter(pk=self.cheap.id).delete()

        self.assertIsNone(self._get().json()["featured_package"])

    def test_a_pppoe_package_is_never_featured(self):
        """This portal sells hotspot bundles; the list it sends holds nothing else."""
        with tenant_context(self.tenant):
            fibre = Package.objects.create(
                tenant=self.tenant, name="20mbps", download_speed=20,
                upload_speed=8, price=Decimal("2500.00"), duration_value=1,
                duration_unit="months", is_hotspot=False)
        self._set(str(fibre.id))

        self.assertIsNone(self._get().json()["featured_package"])

    def test_rubbish_in_the_setting_is_ignored(self):
        for value in ("abc", "12.5", "-1", "None", " "):
            with self.subTest(value=value):
                self._set(value)
                self.assertIsNone(self._get().json()["featured_package"])

    def test_an_id_from_another_operator_is_ignored(self):
        """
        Ids are global and settings are per operator, so a number pasted from
        the wrong console would otherwise feature a package this portal does
        not sell — and leak that it exists.
        """
        other = Tenant.objects.exclude(pk=self.tenant.pk).first()
        if other is None:
            self.skipTest("single-operator fixture")
        with tenant_context(other):
            theirs = Package.objects.create(
                tenant=other, name="Theirs", download_speed=4, upload_speed=2,
                price=Decimal("15.00"), duration_value=1, duration_unit="hours",
                is_hotspot=True)
        self._set(str(theirs.id))

        self.assertIsNone(self._get().json()["featured_package"])

    # ────────────────────────────────────────────────────────── the plumbing

    def test_the_key_is_purged_from_cache_on_write(self):
        """
        The trap config.py annotates by name: a write lands in the database,
        the purge does not mention the key, and the old value is served until
        the TTL runs out. An operator flipping the weekend offer would watch
        the old one stay up and reasonably conclude the control was broken.
        """
        from billing.config import ALL_SETTING_KEYS

        self.assertIn("HOTSPOT_FEATURED_PACKAGE", ALL_SETTING_KEYS)

    def test_an_operator_can_save_it(self):
        from billing.serializers import SystemSettingSerializer

        s = SystemSettingSerializer(
            data={"HOTSPOT_FEATURED_PACKAGE": str(self.day.id)}, partial=True)

        self.assertTrue(s.is_valid(), s.errors)

    def test_an_operator_can_clear_it(self):
        from billing.serializers import SystemSettingSerializer

        s = SystemSettingSerializer(
            data={"HOTSPOT_FEATURED_PACKAGE": ""}, partial=True)

        self.assertTrue(s.is_valid(), s.errors)

    def test_the_settings_page_offers_it(self):
        from billing.views import SystemSettingsView

        self.assertIn("HOTSPOT_FEATURED_PACKAGE", SystemSettingsView.ALL_KEYS)
