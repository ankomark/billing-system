"""
Signing in: the lockout, two-step verification, sign-out, and the record.

Each class is one promise the login makes to an operator, tested through the
HTTP API as the dashboard uses it.
"""

import time
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from billing import login_security as ls
from billing import mfa
from billing.auth_tokens import TenantTokenObtainPairSerializer
from billing.models import LoginEvent, MFABackupCode, SystemSetting, Tenant, User
from billing.tests import make_admin, make_platform_owner

LOGIN = "/api/auth/login/"
LOGIN_MFA = "/api/auth/login/mfa/"


def code_for(user, offset=0):
    user.refresh_from_db()
    return mfa._code_at(user.mfa_secret, mfa.current_step() + offset)


def bearer(user):
    client = APIClient()
    token = TenantTokenObtainPairSerializer.get_token(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def enable_mfa(user):
    user.mfa_secret = mfa.new_secret()
    user.mfa_enabled = True
    user.save()
    return mfa.issue_backup_codes(user)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.get(slug="skylink")
        self.tenant.contact_phone = "254700000001"
        self.tenant.save(update_fields=["contact_phone"])
        self.admin = make_admin("sec_admin")
        self.client = APIClient()
        # Each test gets its own throttle bucket; the 5/min login throttle is
        # tested in LoginThrottleTests and would otherwise end these early.
        self.client.defaults["REMOTE_ADDR"] = f"10.77.{id(self) % 250}.{int(time.time()) % 250}"
        self.alerts = patch("billing.tasks.notification_tasks.notify_customer_task.delay").start()
        self.addCleanup(patch.stopall)
        self.throttle_off = patch("billing.throttles.LoginRateThrottle.allow_request",
                                  return_value=True).start()
        patch("billing.throttles.MFARateThrottle.allow_request", return_value=True).start()

    def login(self, username="sec_admin", password="adminpass", client=None, **extra):
        return (client or self.client).post(
            LOGIN, {"username": username, "password": password}, format="json", **extra)


class APasswordAloneIsNotASessionForAdmins(Base):

    def test_an_admin_without_two_step_is_sent_to_set_it_up(self):
        resp = self.login()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["mfa_setup_required"])
        self.assertNotIn("access", resp.data)
        self.assertNotIn("refresh", resp.data)

    def test_setting_it_up_finishes_signing_in(self):
        ticket = self.login().data["ticket"]

        setup = self.client.post("/api/auth/mfa/setup/", {"ticket": ticket}, format="json")
        self.assertEqual(setup.status_code, 200)
        self.assertTrue(setup.data["qr"].startswith("data:image/svg+xml"))
        self.assertIn("otpauth://totp/", setup.data["otpauth_uri"])

        done = self.client.post("/api/auth/mfa/confirm/",
                                {"ticket": ticket, "code": code_for(self.admin)}, format="json")
        self.assertEqual(done.status_code, 200)
        self.assertIn("access", done.data)
        self.assertEqual(len(done.data["backup_codes"]), 10)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.mfa_enabled)

    def test_a_wrong_code_does_not_switch_it_on(self):
        ticket = self.login().data["ticket"]
        self.client.post("/api/auth/mfa/setup/", {"ticket": ticket}, format="json")
        resp = self.client.post("/api/auth/mfa/confirm/",
                                {"ticket": ticket, "code": "000000"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.mfa_enabled)

    def test_staff_who_are_not_required_still_sign_in_with_a_password(self):
        User.objects.create_user(username="sec_staff", password="staffpass12345",
                                 role=User.TENANT_STAFF, tenant=self.tenant)
        resp = self.login("sec_staff", "staffpass12345")
        self.assertIn("access", resp.data)


class SigningInWithACode(Base):

    def setUp(self):
        super().setUp()
        self.backup = enable_mfa(self.admin)

    def test_the_password_step_gives_a_ticket_not_a_session(self):
        resp = self.login()
        self.assertTrue(resp.data["mfa_required"])
        self.assertNotIn("access", resp.data)

    def test_the_right_code_gives_a_session(self):
        ticket = self.login().data["ticket"]
        resp = self.client.post(LOGIN_MFA, {"ticket": ticket, "code": code_for(self.admin)},
                                format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertEqual(resp.data["role"], User.TENANT_ADMIN)

    def test_a_code_cannot_be_used_twice(self):
        code = code_for(self.admin)
        ticket = self.login().data["ticket"]
        self.assertEqual(self.client.post(
            LOGIN_MFA, {"ticket": ticket, "code": code}, format="json").status_code, 200)
        ticket = self.login().data["ticket"]
        self.assertEqual(self.client.post(
            LOGIN_MFA, {"ticket": ticket, "code": code}, format="json").status_code, 400)

    def test_a_wrong_code_is_refused_and_recorded(self):
        ticket = self.login().data["ticket"]
        resp = self.client.post(LOGIN_MFA, {"ticket": ticket, "code": "123456"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(LoginEvent.objects.filter(
            user=self.admin, outcome=LoginEvent.MFA_FAILED).exists())

    def test_a_backup_code_works_once(self):
        ticket = self.login().data["ticket"]
        resp = self.client.post(LOGIN_MFA, {"ticket": ticket, "code": self.backup[0].lower()},
                                format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["backup_codes_left"], 9)
        ticket = self.login().data["ticket"]
        self.assertEqual(self.client.post(
            LOGIN_MFA, {"ticket": ticket, "code": self.backup[0]}, format="json").status_code, 400)

    def test_a_forged_or_stale_ticket_is_refused(self):
        self.assertEqual(self.client.post(
            LOGIN_MFA, {"ticket": "nonsense", "code": code_for(self.admin)},
            format="json").status_code, 400)
        ticket = self.login().data["ticket"]
        self.admin.invalidate_sessions()      # e.g. the password was changed meanwhile
        self.assertEqual(self.client.post(
            LOGIN_MFA, {"ticket": ticket, "code": code_for(self.admin)},
            format="json").status_code, 400)

    def test_a_setup_ticket_cannot_be_used_to_sign_in(self):
        setup_ticket = mfa.issue_ticket(self.admin, mfa.SETUP)
        self.assertEqual(self.client.post(
            LOGIN_MFA, {"ticket": setup_ticket, "code": code_for(self.admin)},
            format="json").status_code, 400)

    def test_a_working_second_factor_cannot_be_replaced_from_a_session(self):
        resp = bearer(self.admin).post("/api/auth/mfa/setup/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_a_required_role_cannot_turn_it_off(self):
        resp = bearer(self.admin).post(
            "/api/auth/mfa/disable/", {"password": "adminpass", "code": code_for(self.admin)},
            format="json")
        self.assertEqual(resp.status_code, 400)


class TheLockout(Base):

    def test_ten_wrong_passwords_lock_the_account_from_that_address(self):
        for _ in range(ls.PAIR_LIMIT):
            self.assertEqual(self.login(password="wrong").status_code, 401)
        resp = self.login()          # the RIGHT password, now refused
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.data["code"], "locked")

    def test_another_address_is_not_locked_by_it(self):
        for _ in range(ls.PAIR_LIMIT):
            self.login(password="wrong")
        other = APIClient()
        other.defaults["REMOTE_ADDR"] = "10.99.0.1"
        self.assertEqual(self.login(client=other).status_code, 200)

    def test_many_addresses_together_lock_the_account(self):
        for i in range(ls.ACCOUNT_LIMIT):
            c = APIClient()
            c.defaults["REMOTE_ADDR"] = f"10.50.{i // 200}.{i % 200}"
            self.login(password="wrong", client=c)
        fresh = APIClient()
        fresh.defaults["REMOTE_ADDR"] = "10.51.0.1"
        self.assertEqual(self.login(client=fresh).status_code, 429)

    def test_a_username_that_does_not_exist_locks_the_same_way(self):
        """Otherwise 'locked' would tell an attacker which usernames are real."""
        for _ in range(ls.PAIR_LIMIT):
            self.login(username="nobody-here", password="wrong")
        self.assertEqual(self.login(username="nobody-here", password="x").status_code, 429)

    def test_the_owner_is_told_once_when_it_locks(self):
        for _ in range(ls.PAIR_LIMIT + 3):
            self.login(password="wrong")
        self.assertEqual(self.alerts.call_count, 1)
        phone, message = self.alerts.call_args[0][:2]
        self.assertEqual(phone, "254700000001")
        self.assertIn("sec_admin", message)

    def test_wrong_codes_count_towards_it_too(self):
        enable_mfa(self.admin)
        ticket = self.login().data["ticket"]
        for _ in range(ls.PAIR_LIMIT):
            self.client.post(LOGIN_MFA, {"ticket": ticket, "code": "000000"}, format="json")
        resp = self.client.post(LOGIN_MFA, {"ticket": ticket, "code": code_for(self.admin)},
                                format="json")
        self.assertEqual(resp.status_code, 429)

    def test_every_attempt_is_recorded(self):
        self.login(password="wrong")
        event = LoginEvent.objects.get()
        self.assertEqual(event.outcome, LoginEvent.FAILED)
        self.assertEqual(event.user, self.admin)


class TheDjangoAdminLogin(Base):
    """The platform owner's other door, which had no limit and no second step."""

    URL = "/admin/login/"

    def setUp(self):
        super().setUp()
        self.owner = make_platform_owner("sec_owner")
        self.web = self.client_class()   # Django's test client: a form post

    def post(self, password="ownerpass", otp=""):
        return self.web.post(self.URL, {"username": "sec_owner", "password": password,
                                        "otp": otp, "next": "/admin/"},
                             REMOTE_ADDR="10.88.0.1")

    def test_a_required_role_without_two_step_is_turned_away(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 200)          # the form again
        self.assertContains(resp, "Set up two-step sign-in")

    def test_it_asks_for_the_code(self):
        enable_mfa(self.owner)
        self.assertEqual(self.post().status_code, 200)   # no code: refused
        resp = self.post(otp=code_for(self.owner))
        self.assertEqual(resp.status_code, 302)          # in

    def test_it_is_locked_out_like_the_api(self):
        enable_mfa(self.owner)
        for _ in range(ls.PAIR_LIMIT):
            self.post(password="wrong")
        resp = self.post(otp=code_for(self.owner))
        self.assertEqual(resp.status_code, 200)          # refused despite being right


class ConfirmingSensitiveActions(Base):

    def setUp(self):
        super().setUp()
        enable_mfa(self.admin)
        self.api = bearer(self.admin)

    def test_changing_the_till_needs_a_fresh_code(self):
        resp = self.api.put("/api/system/settings/", {"MPESA_SHORTCODE": "999111"}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["code"], "mfa_step_up_required")

        ok = self.api.post("/api/auth/mfa/verify/", {"code": code_for(self.admin)}, format="json")
        self.assertEqual(ok.status_code, 200)
        resp = self.api.put("/api/system/settings/", {"MPESA_SHORTCODE": "999111"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SystemSetting.objects.get(
            tenant=self.tenant, key="MPESA_SHORTCODE").value, "999111")

    def test_harmless_settings_ask_for_nothing(self):
        resp = self.api.put("/api/system/settings/", {"SUPPORT_PHONE": "0700000000"},
                            format="json")
        self.assertEqual(resp.status_code, 200)

    def test_adding_staff_needs_a_fresh_code(self):
        resp = self.api.post("/api/users/", {"username": "new_hand", "role": "tenant_staff",
                                             "password": "a-long-enough-pass"}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["code"], "mfa_step_up_required")
        # Reading the list is not sensitive.
        self.assertEqual(self.api.get("/api/users/").status_code, 200)


class SigningOut(Base):

    def session(self):
        with override_settings(MFA_REQUIRED_ROLES=""):
            return self.login().data

    def client_with(self, access):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return c

    def test_signing_out_ends_the_session_on_the_server(self):
        data = self.session()
        api = self.client_with(data["access"])
        self.assertEqual(api.get("/api/auth/profile/").status_code, 200)

        self.assertEqual(api.post("/api/auth/logout/", {"refresh": data["refresh"]},
                                  format="json").status_code, 204)

        self.assertEqual(api.get("/api/auth/profile/").status_code, 401)
        self.assertEqual(APIClient().post("/api/auth/refresh/", {"refresh": data["refresh"]},
                                          format="json").status_code, 401)

    def test_sign_out_everywhere_ends_every_session(self):
        one, two = self.session(), self.session()
        self.client_with(one["access"]).post("/api/auth/logout-everywhere/")
        self.assertEqual(self.client_with(two["access"]).get("/api/auth/profile/").status_code, 401)
        self.assertEqual(APIClient().post("/api/auth/refresh/", {"refresh": two["refresh"]},
                                          format="json").status_code, 401)

    def test_a_refresh_token_is_good_for_one_renewal(self):
        data = self.session()
        first = APIClient().post("/api/auth/refresh/", {"refresh": data["refresh"]}, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertNotEqual(first.data["refresh"], data["refresh"])
        again = APIClient().post("/api/auth/refresh/", {"refresh": data["refresh"]}, format="json")
        self.assertEqual(again.status_code, 401)

    def test_a_disabled_account_cannot_renew(self):
        data = self.session()
        self.admin.is_active = False
        self.admin.save()
        resp = APIClient().post("/api/auth/refresh/", {"refresh": data["refresh"]}, format="json")
        self.assertEqual(resp.status_code, 401)


class TheRecord(Base):

    @override_settings(MFA_REQUIRED_ROLES="")
    def test_a_sign_in_from_a_new_browser_is_flagged_and_reported(self):
        self.login(HTTP_X_DEVICE_ID="laptop")          # first ever: nothing to compare
        self.login(HTTP_X_DEVICE_ID="laptop")          # same browser
        self.assertEqual(self.alerts.call_count, 0)
        self.login(HTTP_X_DEVICE_ID="someone-elses")   # new
        self.assertEqual(self.alerts.call_count, 1)
        self.assertIn("new device", self.alerts.call_args[0][1])
        self.assertEqual(
            list(LoginEvent.objects.order_by("created_at").values_list("new_device", flat=True)),
            [False, False, True])

    @override_settings(MFA_REQUIRED_ROLES="")
    def test_history_is_your_own_or_your_teams(self):
        staff = User.objects.create_user(username="sec_staff2", password="staffpass12345",
                                         role=User.TENANT_STAFF, tenant=self.tenant)
        self.login()
        self.login("sec_staff2", "staffpass12345")
        other = Tenant.objects.create(name="Elsewhere", slug="elsewhere-sec")
        outsider = make_admin("sec_outsider", tenant=other)
        self.login("sec_outsider")

        own = bearer(self.admin).get("/api/auth/login-history/").data["results"]
        self.assertEqual({r["username"] for r in own}, {"sec_admin"})
        team = bearer(self.admin).get("/api/auth/login-history/?scope=team").data["results"]
        self.assertEqual({r["username"] for r in team}, {"sec_admin", "sec_staff2"})
        # Staff asking for the team get only their own.
        staff_view = bearer(staff).get("/api/auth/login-history/?scope=team").data["results"]
        self.assertEqual({r["username"] for r in staff_view}, {"sec_staff2"})
        self.assertTrue(outsider)


class PasswordRules(TestCase):

    def test_staff_need_twelve_characters_subscribers_eight(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError

        staff = User(username="x", role=User.TENANT_ADMIN)
        customer = User(username="y", role=User.CUSTOMER)
        with self.assertRaises(ValidationError):
            validate_password("Kq7#mZp2vL9", user=staff)        # 11
        validate_password("Kq7#mZp2vL9w", user=staff)           # 12
        validate_password("Kq7#mZp2", user=customer)            # 8

    @override_settings(PASSWORD_BREACH_CHECK=True)
    def test_a_breached_password_is_refused(self):
        import hashlib

        from django.core.exceptions import ValidationError

        from billing.password_validation import BreachedPasswordValidator

        password = "Kq7#mZp2vL9w-breached"
        suffix = hashlib.sha1(password.encode()).hexdigest().upper()[5:]
        fake = MagicMock(text=f"0000000000000000000000000000000000A:1\n{suffix}:42\n")
        fake.raise_for_status.return_value = None
        with patch("requests.get", return_value=fake) as get:
            with self.assertRaises(ValidationError):
                BreachedPasswordValidator().validate(password)
        # Only five characters of the hash left the building.
        self.assertEqual(len(get.call_args[0][0].rsplit("/", 1)[1]), 5)

    @override_settings(PASSWORD_BREACH_CHECK=True)
    def test_it_fails_open_when_the_service_is_down(self):
        from billing.password_validation import BreachedPasswordValidator

        with patch("requests.get", side_effect=OSError("down")):
            BreachedPasswordValidator().validate("Kq7#mZp2vL9w-whatever")


class Totp(TestCase):
    """The RFC 6238 test vector, so the codes match what phones produce."""

    def test_rfc_6238_vector(self):
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        # T = 59s -> step 1 -> 94287082 (8 digits); its last six are 287082.
        self.assertEqual(mfa._code_at(secret, 1), "287082")
        self.assertEqual(mfa.matching_step(secret, "287082", now=59), 1)
        self.assertIsNone(mfa.matching_step(secret, "287082", now=59 + 5 * 30))
