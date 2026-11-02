"""
Signing in, two-step verification, signing out, and the record of it all.

Kept apart from views.py, which is a business-logic module ten thousand lines
long; everything that decides who gets a session lives here, where it can be
read in one sitting. The policy is in login_security.py (lockout, alerts)
and mfa.py (codes, tickets, step-up).
"""

from django.contrib.auth.models import update_last_login
from django.utils import timezone
from rest_framework import exceptions as drf_exceptions
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from . import login_security as ls
from . import mfa
from .auth_tokens import TenantTokenObtainPairSerializer
from .authentication import VERSION_CLAIM, revoke_access_token
from .models import AdminActionLog, LoginEvent, User, record_admin_action
from .security import client_ip
from .throttles import LoginRateThrottle, MFARateThrottle


def _session_for(user):
    """The same token pair and extras the password login has always returned."""
    refresh = TenantTokenObtainPairSerializer.get_token(user)
    update_last_login(None, user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "role": user.role,
        "tenant_id": user.tenant_id,
        "is_platform_staff": user.is_platform_staff,
    }


def _locked_response(request, username, wait):
    ls.record(request, username=username, outcome=LoginEvent.LOCKED,
              user=User.objects.filter(username__iexact=username.strip()).first())
    resp = Response(
        {"detail": ls.lock_message(wait), "code": "locked", "retry_after": wait},
        status=status.HTTP_429_TOO_MANY_REQUESTS)
    resp["Retry-After"] = str(wait)
    return resp


def _bad_code(request, user):
    ls.failed(request, user.username, outcome=LoginEvent.MFA_FAILED)
    return Response(
        {"detail": "That code is not right. Check the app and try again.",
         "code": "mfa_invalid"},
        status=status.HTTP_400_BAD_REQUEST)


def _ticket_expired():
    return Response(
        {"detail": "That sign-in has expired. Please start again.",
         "code": "ticket_invalid"},
        status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    """
    Step one: the password.

    For an account without two-step this is the whole sign-in, as before. For
    one with it, the answer is a ticket for step two; for a role that needs it
    and has not set it up, a ticket to set it up. A password on its own never
    yields a session for either.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        username = str(request.data.get("username") or "")
        wait = ls.locked_for(username, client_ip(request))
        if wait:
            return _locked_response(request, username, wait)

        serializer = TenantTokenObtainPairSerializer(
            data=request.data, context={"request": request})
        try:
            # A wrong password is counted by the user_login_failed signal,
            # which authenticate() fires -- see login_security.
            serializer.is_valid(raise_exception=True)
        except drf_exceptions.AuthenticationFailed:
            # simplejwt raises DRF's own AuthenticationFailed here. Answered
            # directly: this view has no authentication classes, so left to
            # DRF it would become a 403.
            return Response(
                {"detail": "Invalid username or password.", "code": "invalid_credentials"},
                status=status.HTTP_401_UNAUTHORIZED)

        user = serializer.user
        ls.clear_failures(username, client_ip(request))

        if user.mfa_enabled:
            return Response({"mfa_required": True,
                             "ticket": mfa.issue_ticket(user, mfa.VERIFY)})
        if mfa.is_required(user):
            return Response({"mfa_setup_required": True,
                             "ticket": mfa.issue_ticket(user, mfa.SETUP)})

        ls.record(request, username=user.username, outcome=LoginEvent.SUCCESS,
                  user=user, method=LoginEvent.PASSWORD)
        return Response(_session_for(user))


class MFALoginView(APIView):
    """Step two: the ticket from step one, and a code."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [MFARateThrottle]

    def post(self, request):
        user = mfa.read_ticket(request.data.get("ticket"), mfa.VERIFY)
        if user is None:
            return _ticket_expired()
        wait = ls.locked_for(user.username, client_ip(request))
        if wait:
            return _locked_response(request, user.username, wait)

        method = mfa.check_code(user, request.data.get("code"))
        if method is None:
            return _bad_code(request, user)

        ls.clear_failures(user.username, client_ip(request))
        mfa.mark_recent(user)
        ls.record(request, username=user.username, outcome=LoginEvent.SUCCESS,
                  user=user, method=method)
        data = _session_for(user)
        if method == LoginEvent.BACKUP_CODE:
            data["backup_codes_left"] = mfa.backup_codes_left(user)
        return Response(data)


def _setup_user(request):
    """Who is setting up: a signed-in account, or the holder of a setup ticket."""
    if request.user and request.user.is_authenticated:
        return request.user
    return mfa.read_ticket(request.data.get("ticket"), mfa.SETUP)


class MFASetupView(APIView):
    """
    Start setting up: a new secret, and the QR code that carries it.

    Nothing is switched on yet. The secret is only trusted once the holder
    proves their app makes the right codes from it -- MFAConfirmView.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [MFARateThrottle]

    def post(self, request):
        user = _setup_user(request)
        if user is None:
            return _ticket_expired()
        if user.mfa_enabled:
            # Replacing a working secret from a session alone would let
            # anyone holding a stolen session take the second factor over.
            return Response(
                {"detail": "Two-step sign-in is already on. Turn it off first "
                           "to move it to another phone."},
                status=status.HTTP_400_BAD_REQUEST)

        secret = mfa.new_secret()
        user.mfa_secret = secret
        user.mfa_last_step = 0
        user.save(update_fields=["mfa_secret", "mfa_last_step"])
        uri = mfa.otpauth_uri(user, secret)
        return Response({"secret": secret, "otpauth_uri": uri, "qr": mfa.qr_svg(uri)})


class MFAConfirmView(APIView):
    """
    Finish setting up: a code from the app proves it has the secret.

    Answers with the backup codes, which are shown this once and never again;
    and, when this was the last step of signing in, with the session too.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [MFARateThrottle]

    def post(self, request):
        via_ticket = not (request.user and request.user.is_authenticated)
        user = _setup_user(request)
        if user is None:
            return _ticket_expired()
        if user.mfa_enabled:
            return Response({"detail": "Two-step sign-in is already on."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not user.mfa_secret:
            return Response({"detail": "Start the setup first."},
                            status=status.HTTP_400_BAD_REQUEST)
        wait = ls.locked_for(user.username, client_ip(request))
        if wait:
            return _locked_response(request, user.username, wait)

        if mfa.check_code(user, request.data.get("code")) is None:
            return _bad_code(request, user)

        user.mfa_enabled = True
        user.mfa_enabled_at = timezone.now()
        user.save(update_fields=["mfa_enabled", "mfa_enabled_at"])
        codes = mfa.issue_backup_codes(user)
        mfa.mark_recent(user)
        record_admin_action(user, AdminActionLog.ENABLE_MFA, target_user=user)

        data = {"backup_codes": codes}
        if via_ticket:
            ls.clear_failures(user.username, client_ip(request))
            ls.record(request, username=user.username, outcome=LoginEvent.SUCCESS,
                      user=user, method=LoginEvent.TOTP)
            data.update(_session_for(user))
        return Response(data)


class MFAStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            "enabled": user.mfa_enabled,
            "required": mfa.is_required(user),
            "enabled_at": user.mfa_enabled_at,
            "backup_codes_left": mfa.backup_codes_left(user) if user.mfa_enabled else 0,
        })


class MFADisableView(APIView):
    """Turn two-step off. Needs the password and a code, and is refused for roles that must have it."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [MFARateThrottle]

    def post(self, request):
        user = request.user
        if mfa.is_required(user):
            return Response(
                {"detail": "Two-step sign-in is required for your role and "
                           "cannot be turned off."},
                status=status.HTTP_400_BAD_REQUEST)
        if not user.mfa_enabled:
            return Response(status=status.HTTP_204_NO_CONTENT)
        if not user.check_password(request.data.get("password") or ""):
            return Response({"detail": "That is not your current password."},
                            status=status.HTTP_400_BAD_REQUEST)
        if mfa.check_code(user, request.data.get("code")) is None:
            return _bad_code(request, user)

        _clear_mfa(user)
        record_admin_action(user, AdminActionLog.DISABLE_MFA, target_user=user)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _clear_mfa(user):
    user.mfa_enabled = False
    user.mfa_secret = ""
    user.mfa_last_step = 0
    user.mfa_enabled_at = None
    user.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_last_step", "mfa_enabled_at"])
    user.mfa_backup_codes.all().delete()


class BackupCodesView(APIView):
    """A fresh set of backup codes, replacing the old ones. Needs a code."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [MFARateThrottle]

    def post(self, request):
        user = request.user
        if not user.mfa_enabled:
            return Response({"detail": "Two-step sign-in is not on."},
                            status=status.HTTP_400_BAD_REQUEST)
        if mfa.check_code(user, request.data.get("code")) is None:
            return _bad_code(request, user)
        return Response({"backup_codes": mfa.issue_backup_codes(user)})


class StepUpView(APIView):
    """
    Confirm a code mid-session, for the actions that ask for one.

    One code covers ten minutes of them, so changing several settings in a
    row is one interruption, not one per click.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [MFARateThrottle]

    def post(self, request):
        user = request.user
        wait = ls.locked_for(user.username, client_ip(request))
        if wait:
            return _locked_response(request, user.username, wait)
        if mfa.check_code(user, request.data.get("code")) is None:
            return _bad_code(request, user)
        mfa.mark_recent(user)
        return Response({"ok": True, "valid_for": mfa.STEP_UP_SECONDS})


# ─────────────────────────────────────────────────────────── signing out

class LogoutView(APIView):
    """
    End this session on the server, not only in the browser.

    Clearing the browser's copy was all logging out used to do; a token
    copied before that kept working until it expired. The refresh token is
    blacklisted and the access token is refused from now on.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        raw = request.data.get("refresh")
        if raw:
            try:
                token = RefreshToken(raw)
                if str(token.get("user_id")) == str(request.user.pk):
                    token.blacklist()
            except TokenError:
                pass   # already expired or already blacklisted: nothing to end
        if request.auth is not None:
            revoke_access_token(request.auth)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutEverywhereView(APIView):
    """
    End every session this account has, on every device, this one included.

    For the moment somebody suspects their account is being used by someone
    else. The same mechanism a password change uses -- token_version -- so it
    takes effect on the very next request each device makes.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        request.user.invalidate_sessions()
        record_admin_action(request.user, AdminActionLog.SIGN_OUT_EVERYWHERE,
                            target_user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CheckedTokenRefreshSerializer(TokenRefreshSerializer):
    """
    Refuses to renew a session the account has since ended.

    Rotation mints a new pair from the old refresh token's claims, so without
    this a refresh token from before a password change would go on producing
    access tokens -- each refused on use, but a disabled account kept
    "signed in" all the same.
    """

    def validate(self, attrs):
        try:
            token = RefreshToken(attrs["refresh"])
        except TokenError as exc:
            raise InvalidToken(exc.args[0])
        user = User.objects.filter(pk=token.get("user_id")).first()
        if user is None or not user.is_active:
            raise InvalidToken("This account is no longer active.")
        version = token.get(VERSION_CLAIM)
        if version is not None and version != user.token_version:
            raise InvalidToken("This session has been ended. Please sign in again.")
        return super().validate(attrs)


class CheckedTokenRefreshView(TokenRefreshView):
    serializer_class = CheckedTokenRefreshSerializer


# ─────────────────────────────────────────────────────────── the record

class LoginHistoryView(APIView):
    """
    Recent sign-ins: your own, or -- for an operator admin asking for
    ?scope=team -- everyone's on your operator's accounts.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        events = LoginEvent.objects.all()
        if request.query_params.get("scope") == "team" and user.is_tenant_admin:
            events = events.filter(tenant_id=user.tenant_id)
        else:
            events = events.filter(user=user)
        rows = events.order_by("-created_at")[:50]
        return Response({"results": [
            {
                "id": e.id,
                "at": e.created_at,
                "username": e.username,
                "outcome": e.outcome,
                "outcome_label": e.get_outcome_display(),
                "method": e.method,
                "ip": e.ip,
                "user_agent": e.user_agent,
                "new_device": e.new_device,
            }
            for e in rows
        ]})
