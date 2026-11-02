"""
Two-step sign-in: the password, and then a code from the holder's phone.

Codes are TOTP (RFC 6238) -- what Google Authenticator, Microsoft
Authenticator, Authy and 1Password all produce: six digits, a new one every
thirty seconds, derived from a secret shared once through a QR code. Nothing
is sent by SMS, so there is no message to intercept and no credit to run out.

The flow, and why it is shaped this way:

  1. POST auth/login/ with the password. For an account that uses two-step,
     the answer is NOT a session -- it is a ticket: a signed, five-minute
     statement that this password was right. A stolen password alone
     therefore gets an attacker nothing they can use.

  2. POST auth/login/mfa/ with the ticket and a code. Only now are tokens
     issued.

Roles in MFA_REQUIRED_ROLES that have not set it up yet get a ticket for
setup instead, and finish signing in by setting it up. There is no way round
it from the password alone.

Separately, the most dangerous actions -- changing payment credentials,
creating or changing staff accounts -- ask for a fresh code even mid-session.
A session left open on a shared computer should not be enough to redirect an
operator's M-Pesa money. See require_recent_mfa.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

STEP = 30                 # seconds per code
DIGITS = 6
DRIFT = 1                 # steps either side accepted, for phone clocks that wander
TICKET_MAX_AGE = 5 * 60   # seconds a password-checked ticket stays good
STEP_UP_SECONDS = 10 * 60 # how long one code covers the sensitive actions
BACKUP_CODE_COUNT = 10
ISSUER = "SmartBill"

TICKET_SALT = "billing.mfa.ticket"
VERIFY = "verify"
SETUP = "setup"


# ───────────────────────────────────────────────────────────────── policy

def required_roles():
    raw = getattr(settings, "MFA_REQUIRED_ROLES", "")
    return {r.strip() for r in raw.split(",") if r.strip()}


def is_required(user):
    return user.role in required_roles()


# ─────────────────────────────────────────────────────────────────── TOTP

def new_secret():
    # 160 bits, the size RFC 4226 recommends, in the base32 every app reads.
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** DIGITS)).zfill(DIGITS)


def current_step(now=None):
    return int((now if now is not None else time.time()) // STEP)


def matching_step(secret, code, now=None):
    """The step `code` belongs to, within the allowed drift; None if none."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    here = current_step(now)
    for step in range(here - DRIFT, here + DRIFT + 1):
        if hmac.compare_digest(_code_at(secret, step), code):
            return step
    return None


def otpauth_uri(user, secret):
    label = quote(f"{ISSUER}:{user.username}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"
            f"&digits={DIGITS}&period={STEP}")


def qr_svg(uri):
    """The QR code an app scans, as an SVG data URI -- no image files, no CDN."""
    import segno
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


# ───────────────────────────────────────────────────────────── backup codes

def _hash_backup(code):
    normalised = "".join(ch for ch in code.upper() if ch.isalnum())
    return hmac.new(settings.SECRET_KEY.encode(), normalised.encode(),
                    hashlib.sha256).hexdigest()


def issue_backup_codes(user):
    """Replace this account's backup codes. Returns the codes, once, in clear."""
    from .models import MFABackupCode

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O or 1/I to misread
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    MFABackupCode.objects.filter(user=user).delete()
    MFABackupCode.objects.bulk_create(
        [MFABackupCode(user=user, code_hash=_hash_backup(c)) for c in codes])
    return codes


def backup_codes_left(user):
    return user.mfa_backup_codes.filter(used_at__isnull=True).count()


# ──────────────────────────────────────────────────────────────── checking

def check_code(user, code):
    """
    Whether `code` is a valid authenticator code or unused backup code for
    this account. Returns the method that matched, or None.

    An authenticator code is accepted once: its step is remembered, and
    nothing at or before it is accepted again. Saved with an UPDATE that only
    succeeds if no one else has moved the step on meanwhile, so two requests
    racing with the same code cannot both win.
    """
    from .models import LoginEvent, MFABackupCode, User

    if not user.mfa_secret:
        return None

    step = matching_step(user.mfa_secret, code)
    if step is not None:
        moved = User.objects.filter(pk=user.pk, mfa_last_step__lt=step).update(
            mfa_last_step=step)
        if moved:
            user.mfa_last_step = step
            return LoginEvent.TOTP
        return None

    normalised = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    if user.mfa_enabled and len(normalised) == 10:
        used = MFABackupCode.objects.filter(
            user=user, code_hash=_hash_backup(normalised), used_at__isnull=True,
        ).update(used_at=timezone.now())
        if used:
            return LoginEvent.BACKUP_CODE
    return None


# ───────────────────────────────────────────────────────────────── tickets

def issue_ticket(user, purpose):
    """
    A signed note that this account's password was just checked. Carries the
    token_version, so a password change made meanwhile voids it.
    """
    return signing.dumps(
        {"uid": user.pk, "tv": user.token_version, "p": purpose},
        salt=TICKET_SALT, compress=True)


def read_ticket(ticket, purpose):
    """The user a ticket was issued for, or None if it is bad, old or stale."""
    from .models import User

    try:
        data = signing.loads(ticket or "", salt=TICKET_SALT, max_age=TICKET_MAX_AGE)
    except signing.BadSignature:
        return None
    if data.get("p") != purpose:
        return None
    user = User.objects.filter(pk=data.get("uid"), is_active=True).select_related("tenant").first()
    if user is None or user.token_version != data.get("tv"):
        return None
    return user


# ──────────────────────────────────────────────────── step-up for actions

def _recent_key(user):
    return f"mfa:recent:{user.pk}:{user.token_version}"


def mark_recent(user):
    cache.set(_recent_key(user), 1, STEP_UP_SECONDS)


def is_recent(user):
    return bool(cache.get(_recent_key(user)))


class StepUpRequired(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "mfa_step_up_required"

    def __init__(self):
        super().__init__({
            "detail": "Enter a code from your authenticator app to continue.",
            "code": self.default_code,
        })


def require_recent_mfa(request):
    """
    Call at the top of a sensitive write. Raises StepUpRequired unless this
    account confirmed a code in the last ten minutes. The frontend catches
    that answer, asks for a code, and retries -- see services/api.js.

    Accounts without two-step set up are let through: they cannot produce a
    code, and every role that matters is made to set it up at sign-in.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.mfa_enabled:
        return
    if not is_recent(user):
        raise StepUpRequired()
