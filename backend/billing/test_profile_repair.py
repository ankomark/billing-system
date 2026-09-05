"""
A package's speed has to reach the router when it changes.

ensure_hotspot_profile returned the moment it saw a profile with the right
name, and the name is built from the package id and the device count --
neither of which changes when an operator edits a *speed*. So changing a
package from 6M to 2M updated the database, reported success on the dashboard,
and left every router in the estate still handing out 6M. Nothing said the two
disagreed.

Found on 2026-09-05 while throttling every hotspot package to 2M for capacity:
the change would have been cosmetic everywhere it mattered.

Its PPPoE twin has always repaired stale profiles, and says so in its own
docstring -- "a profile left behind by a package whose speed has since changed
is indistinguishable by name from a correct one". Only the hotspot side
skipped it.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.models import Package, RouterDevice, Tenant
from billing.tenancy import tenant_context


class _Profiles(list):
    """A RouterOS path that records what was written to it."""

    def __init__(self, rows):
        super().__init__(rows)
        self.updated = []
        self.added = []

    def update(self, **kwargs):
        self.updated.append(kwargs)

    def add(self, **kwargs):
        self.added.append(kwargs)


class HotspotProfileRepairTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.get(slug="skylink")
        with tenant_context(self.tenant):
            self.router = RouterDevice.objects.create(
                tenant=self.tenant, name="r1", ip_address="10.0.0.9",
                username="u", password="p")
            self.package = Package.objects.create(
                tenant=self.tenant, name="3hrs unlimited", download_speed=2,
                upload_speed=2, price=Decimal("10.00"), duration_value=3,
                duration_unit="hours", is_hotspot=True, max_devices=1)

    def _run(self, existing_rows):
        profiles = _Profiles(existing_rows)
        api = MagicMock()
        api.path.return_value = profiles
        with patch("billing.router_profiles.connect_router", return_value=api):
            from billing.router_profiles import ensure_hotspot_profile
            name = ensure_hotspot_profile(self.router, self.package)
        return name, profiles

    def test_a_stale_speed_is_corrected_on_the_router(self):
        """The whole point: 6M on the router, 2M in the database."""
        name = f"HOTSPOT_PKG_{self.package.id}_D1"
        _, profiles = self._run([
            {".id": "*1", "name": name, "rate-limit": "6M/6M", "shared-users": "1"}
        ])

        self.assertEqual(len(profiles.updated), 1,
                         "a profile with the wrong speed was left alone")
        self.assertEqual(profiles.updated[0]["rate-limit"], "2M/2M")
        self.assertEqual(profiles.updated[0][".id"], "*1")
        self.assertFalse(profiles.added, "it added a duplicate instead of repairing")

    def test_a_correct_profile_is_not_rewritten(self):
        """Provisioning runs constantly; it must not write on every call."""
        name = f"HOTSPOT_PKG_{self.package.id}_D1"
        _, profiles = self._run([
            {".id": "*1", "name": name, "rate-limit": "2M/2M", "shared-users": "1"}
        ])
        self.assertEqual(profiles.updated, [])
        self.assertEqual(profiles.added, [])

    def test_a_missing_profile_is_still_created(self):
        name, profiles = self._run([])
        self.assertEqual(len(profiles.added), 1)
        self.assertEqual(profiles.added[0]["rate-limit"], "2M/2M")
        self.assertEqual(profiles.added[0]["name"], name)

    def test_the_created_profile_carries_no_comment(self):
        """
        RouterOS has no comment property on /ip/hotspot/user/profile and
        rejects the whole request rather than ignoring it, which used to mean
        no hotspot activation worked at all.
        """
        _, profiles = self._run([])
        self.assertNotIn("comment", profiles.added[0])

    def test_the_device_count_still_reaches_the_router(self):
        with tenant_context(self.tenant):
            self.package.max_devices = 3
            self.package.save(update_fields=["max_devices"])

        name, profiles = self._run([])
        self.assertTrue(name.endswith("_D3"))
        self.assertEqual(profiles.added[0]["shared-users"], "3")
