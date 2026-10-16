"""The Smart Mapping API: who may do what, and that operators never see each other."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from billing.auth_tokens import TenantTokenObtainPairSerializer
from billing.models import Tenant, User
from billing.tenancy import tenant_context

from fiber.models import Olt, Onu
from fiber.services.collector import poll_olt

from .helpers import FiberFixture


def client_for(user):
    c = APIClient()
    token = TenantTokenObtainPairSerializer.get_token(user).access_token
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


@patch("billing.tasks.alert_tasks.notify_admin_task.delay")
@patch("billing.notifications.send_bulk_sms", return_value={"sent": 0})
class FiberApiTests(FiberFixture, TestCase):
    def setUp(self):
        self.make_world()
        self.c, _ = self.customer_with_device("AA:BB:CC:00:00:05")
        self.sim(clients={"1:2": ["AA:BB:CC:00:00:05"]})
        self.olt.refresh_from_db()
        poll_olt(self.olt)
        self.admin = User.objects.create_user(username="fa", password="x", role=User.TENANT_ADMIN,
                                              tenant=self.tenant, is_staff=True)
        self.staff = User.objects.create_user(username="fs", password="x", role=User.TENANT_STAFF,
                                              tenant=self.tenant)
        with tenant_context(self.tenant):
            self.onu = Onu.objects.get(pon_port=1, onu_index=2)

    def test_anonymous_is_refused(self, *_):
        self.assertIn(APIClient().get("/api/fiber/overview/").status_code, (401, 403))

    def test_staff_can_read_everything(self, *_):
        c = client_for(self.staff)
        for url in ("/api/fiber/overview/", "/api/fiber/olts/", "/api/fiber/onus/", "/api/fiber/map/",
                    "/api/fiber/outages/", "/api/fiber/tickets/", "/api/fiber/splitters/suggest/",
                    "/api/fiber/reports/ports/", "/api/fiber/reports/monthly/", f"/api/fiber/onus/{self.onu.pk}/"):
            self.assertEqual(c.get(url).status_code, 200, url)

    def test_onu_detail_names_its_customer(self, *_):
        r = client_for(self.staff).get(f"/api/fiber/onus/{self.onu.pk}/").json()
        self.assertEqual([x["id"] for x in r["customers"]], [self.c.pk])
        self.assertEqual(r["position"], "PON1:2")

    def test_search_finds_an_onu_by_its_customers_phone(self, *_):
        r = client_for(self.staff).get(f"/api/fiber/onus/?q={self.c.phone}").json()
        self.assertEqual([x["id"] for x in r], [self.onu.pk])

    def test_staff_cannot_change_olts_but_admin_can_and_secrets_never_return(self, *_):
        body = {"name": "HiOSO", "vendor": "hioso", "mgmt_ip": "10.0.0.3", "snmp_community": "s3cret",
                "cli_username": "admin", "cli_password": "pw"}
        self.assertEqual(client_for(self.staff).post("/api/fiber/olts/", body, format="json").status_code, 403)
        r = client_for(self.admin).post("/api/fiber/olts/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn("s3cret", r.content.decode())
        self.assertTrue(r.json()["has_snmp_community"])
        olt = Olt.objects.all_tenants().get(name="HiOSO")
        self.assertEqual(olt.snmp_community, "s3cret")
        # blank on update keeps the stored secret
        client_for(self.admin).patch(f"/api/fiber/olts/{olt.pk}/", {"snmp_community": ""}, format="json")
        olt.refresh_from_db()
        self.assertEqual(olt.snmp_community, "s3cret")

    @override_settings(DEBUG=False)
    def test_a_simulated_olt_cannot_be_registered_in_production(self, *_):
        r = client_for(self.admin).post("/api/fiber/olts/", {"name": "x", "vendor": "simulated",
                                                             "mgmt_ip": "10.0.0.9"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_technician_saves_a_location(self, *_):
        c = client_for(self.staff)
        bad = c.post(f"/api/fiber/onus/{self.onu.pk}/locate/", {"latitude": 200, "longitude": 1}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = c.post(f"/api/fiber/onus/{self.onu.pk}/locate/",
                    {"latitude": -0.5273, "longitude": 34.4571, "note": "blue gate"}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.onu.refresh_from_db()
        self.assertEqual((self.onu.latitude, self.onu.location_note, self.onu.located_by),
                         (-0.5273, "blue gate", "fs"))

    def test_maintenance_window_announces_and_validates(self, sms, _):
        c = client_for(self.admin)
        bad = c.post("/api/fiber/maintenance/", {"olt": self.olt.pk, "reason": "x",
                                                 "starts_at": "2026-11-02T10:00:00Z",
                                                 "ends_at": "2026-11-02T09:00:00Z"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = c.post("/api/fiber/maintenance/", {"olt": self.olt.pk, "pon_port": 1, "reason": "splicing",
                                                "starts_at": "2026-11-02T10:00:00Z",
                                                "ends_at": "2026-11-02T12:00:00Z"}, format="json")
        self.assertEqual(ok.status_code, 201, ok.content)
        sms.assert_called_once()
        self.assertEqual([p for p, _ in sms.call_args.args[0]], [self.c.phone])

    def test_another_operator_sees_none_of_it(self, *_):
        other = Tenant.objects.create(name="Other ISP", slug="other-isp")
        stranger = User.objects.create_user(username="other", password="x", role=User.TENANT_ADMIN,
                                            tenant=other, is_staff=True)
        c = client_for(stranger)
        self.assertEqual(c.get(f"/api/fiber/onus/{self.onu.pk}/").status_code, 404)
        self.assertEqual(c.get("/api/fiber/onus/").json(), [])
        self.assertEqual(c.get("/api/fiber/overview/").json()["olts"], [])
        r = c.post(f"/api/fiber/onus/{self.onu.pk}/locate/", {"latitude": 1, "longitude": 1}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_portal_line_check(self, *_):
        r = APIClient().get(f"/api/hotspot/line-check/?mac=aa:bb:cc:00:00:05&t={self.tenant.public_token}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        unknown = APIClient().get(f"/api/hotspot/line-check/?mac=11:22:33:44:55:66&t={self.tenant.public_token}")
        self.assertEqual(unknown.json()["status"], "unknown")
        self.assertEqual(APIClient().get("/api/hotspot/line-check/").status_code, 400)
