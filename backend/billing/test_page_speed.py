"""
The changes made to make the dashboard fast, pinned so they stay correct.

Speed is easy to buy with wrong answers -- a cache that hands one operator
another's figures, compression on a response carrying a secret. These hold
the line on both.
"""

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from billing.auth_tokens import TenantTokenObtainPairSerializer
from billing.models import Tenant
from billing.tests import make_admin


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=
                  f"Bearer {TenantTokenObtainPairSerializer.get_token(user).access_token}")
    return c


@override_settings(RESPONSE_CACHE_ENABLED=True)
class TheReportCache(TestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.mine = make_admin("speed_admin")
        other = Tenant.objects.create(name="Other Speed", slug="other-speed")
        self.theirs = make_admin("speed_other", tenant=other)

    def test_a_second_look_is_served_from_the_cache(self):
        api = client_for(self.mine)
        with patch_totals(lambda **kw: {"n": 1}) as calls:
            first = api.get("/api/admin/usage/totals/")
            second = api.get("/api/admin/usage/totals/")
        self.assertEqual(first.data, second.data)
        self.assertEqual(len(calls), 1)

    def test_operators_never_share_an_entry(self):
        with patch_totals(lambda **kw: {"n": len(kw)}) as calls:
            client_for(self.mine).get("/api/admin/usage/totals/")
            client_for(self.theirs).get("/api/admin/usage/totals/")
        self.assertEqual(len(calls), 2)

    def test_different_questions_never_share_an_entry(self):
        api = client_for(self.mine)
        with patch_totals(lambda **kw: {}) as calls:
            api.get("/api/admin/usage/totals/")
            api.get("/api/admin/usage/totals/?station=1")
        self.assertEqual(len(calls), 2)

    @override_settings(RESPONSE_CACHE_ENABLED=False)
    def test_it_can_be_switched_off(self):
        api = client_for(self.mine)
        with patch_totals(lambda **kw: {}) as calls:
            api.get("/api/admin/usage/totals/")
            api.get("/api/admin/usage/totals/")
        self.assertEqual(len(calls), 2)


class patch_totals:
    """Count how often the real work runs behind the usage totals endpoint."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def __enter__(self):
        from unittest.mock import patch

        def fake(**kwargs):
            self.calls.append(kwargs)
            return self.fn(**kwargs)

        self._p = patch("billing.views.estate_usage_totals", side_effect=fake)
        self._p.start()
        return self.calls

    def __exit__(self, *exc):
        self._p.stop()


class Compression(TestCase):

    def setUp(self):
        self.api = client_for(make_admin("gzip_admin"))

    def test_api_answers_are_compressed(self):
        # Long enough to be worth it; GZipMiddleware skips tiny bodies.
        resp = self.api.get("/api/system/settings/", HTTP_ACCEPT_ENCODING="gzip")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get("Content-Encoding"), "gzip")
        import gzip
        self.assertIn(b"{", gzip.decompress(resp.content))

    def test_sign_in_answers_are_never_compressed(self):
        """They carry tokens; see ApiGZipMiddleware on BREACH."""
        resp = APIClient().post("/api/auth/login/",
                                {"username": "nobody-x", "password": "y" * 40},
                                format="json", HTTP_ACCEPT_ENCODING="gzip")
        self.assertIsNone(resp.get("Content-Encoding"))

    def test_a_client_that_does_not_ask_gets_plain_text(self):
        resp = self.api.get("/api/system/settings/")
        self.assertIsNone(resp.get("Content-Encoding"))
