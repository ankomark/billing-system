"""
Who is trying to get in, and when to stop them.

The login endpoint already had a per-address throttle, five a minute. That
bounds one machine; it does nothing against many machines taking turns at one
account, and the Django admin login had no limit of any kind -- the platform
owner account, which can see every operator, could be guessed at there as fast
as the network allowed.

This adds two counters and a record:

  * per account AND address -- ten wrong passwords for one username from one
    address locks that pair for fifteen minutes. The person at the keyboard
    who has forgotten their password meets this one.

  * per account, from anywhere -- fifty in an hour locks the account itself
    for fifteen minutes. This is what a distributed attack meets. It is set
    well above anything a person does by hand, because a lock that is easy to
    trip is a way for anyone to keep an operator out of their own dashboard.

  * per address, across every username -- thirty wrong attempts in an hour
    from one address blocks that address from signing in for an hour. The
    two counters above are per username, so an attacker trying one common
    password against every account ("password spraying") never trips them;
    this is what stops that.

  * LoginEvent -- every attempt, kept so a person can read it later.

And a shape check before any of it: a username or password that could not be
real -- too long, odd characters, an oversized request -- is refused without
touching the database and counted against the address like a wrong password.

Counted by the username as typed, whether or not such an account exists, so
that "locked" can be said out loud without telling anyone which usernames are
real.

Both login paths go through here: the API login view checks before it does any
work, and LockoutModelBackend covers everything that calls authenticate(),
the Django admin included.

No deliberate delay on a failure. The web workers are synchronous, so a sleep
in the request would hand an attacker a way to tie them all up; the counters
do the job a delay would.
"""

import hashlib
import logging
import re
import time

from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

from .security import client_ip

logger = logging.getLogger(__name__)

PAIR_LIMIT = 10           # wrong attempts for one username from one address
PAIR_WINDOW = 15 * 60
ACCOUNT_LIMIT = 50        # wrong attempts for one username from anywhere
ACCOUNT_WINDOW = 60 * 60
LOCK_SECONDS = 15 * 60
IP_LIMIT = 30             # wrong attempts from one address, any usernames
IP_WINDOW = 60 * 60
IP_BLOCK_SECONDS = 60 * 60

# What a sign-in may look like. Django usernames are at most 150 characters of
# letters, digits and @ . + - _ ; nothing else can match an account, so
# anything else is refused before it reaches the database, a log line or an
# admin page. Passwords are capped because hashing is deliberately slow: a
# megabyte "password" costs the server real time to reject.
MAX_USERNAME = 150
MAX_PASSWORD = 128
MAX_BODY_BYTES = 4096
USERNAME_RE = re.compile(r"^[\w.@+-]+$")

# Roles whose new-device sign-ins are worth a text message. Staff sign in
# often and their admin can read the list; a customer's portal login is not a
# security event for the operator.
ALERT_ROLES = ("platform_owner", "platform_staff", "tenant_admin")


def _h(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def _norm(username):
    return (username or "").strip().lower()


def _keys(username, ip):
    u = _h(_norm(username))
    return {
        "ip_count": f"login:fail:ip:{_h(ip)}",
        "ip_block": f"login:block:ip:{_h(ip)}",
        "pair_count": f"login:fail:pair:{u}:{_h(ip)}",
        "pair_lock": f"login:lock:pair:{u}:{_h(ip)}",
        "acct_count": f"login:fail:acct:{u}",
        "acct_lock": f"login:lock:acct:{u}",
    }


def _incr(key, window):
    # add() is a no-op when the key exists, so the window starts at the first
    # failure and is not pushed back by later ones.
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:
        # Expired between add and incr.
        cache.set(key, 1, window)
        return 1


def locked_for(username, ip):
    """Seconds until this username may try again from this address; 0 if now."""
    k = _keys(username, ip)
    until = max(cache.get(k["pair_lock"]) or 0, cache.get(k["acct_lock"]) or 0,
                cache.get(k["ip_block"]) or 0)
    return max(0, int(until - time.time()))


def note_failure(username, ip):
    """
    Count one wrong attempt. Returns True when this one tripped a lock, so the
    caller can tell the account's owner once rather than on every attempt.
    """
    k = _keys(username, ip)
    tripped = False
    until = time.time() + LOCK_SECONDS
    if _incr(k["pair_count"], PAIR_WINDOW) >= PAIR_LIMIT and not cache.get(k["pair_lock"]):
        cache.set(k["pair_lock"], until, LOCK_SECONDS)
        cache.delete(k["pair_count"])
        tripped = True
    if _incr(k["acct_count"], ACCOUNT_WINDOW) >= ACCOUNT_LIMIT and not cache.get(k["acct_lock"]):
        cache.set(k["acct_lock"], until, LOCK_SECONDS)
        cache.delete(k["acct_count"])
        tripped = True
    if ip and _incr(k["ip_count"], IP_WINDOW) >= IP_LIMIT and not cache.get(k["ip_block"]):
        cache.set(k["ip_block"], time.time() + IP_BLOCK_SECONDS, IP_BLOCK_SECONDS)
        cache.delete(k["ip_count"])
        logger.warning("[login] blocked %s after %s failed sign-ins", ip, IP_LIMIT)
    return tripped


def malformed(username, password):
    """
    Why this cannot be a real sign-in, or None if it could be.

    Both must be plain strings: a JSON body can carry a list or an object
    where a string belongs, and that should be refused here rather than
    discovered deep inside the auth stack.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        return "not text"
    if not username or not password:
        return "empty"
    if len(username) > MAX_USERNAME or len(password) > MAX_PASSWORD:
        return "too long"
    if not USERNAME_RE.match(username.strip()):
        return "bad characters"
    return None


def clear_failures(username, ip):
    """
    A correct password from this address forgives this address's mistakes.
    Not the account-wide count: someone guessing from elsewhere is unaffected
    by the owner signing in.
    """
    k = _keys(username, ip)
    cache.delete_many([k["pair_count"]])


def lock_message(seconds):
    minutes = max(1, (seconds + 59) // 60)
    return (
        "Too many failed sign-in attempts. Try again in "
        f"{minutes} minute{'s' if minutes != 1 else ''}."
    )


# ─────────────────────────────────────────────────────────────── the record

def device_of(request):
    raw = (request.META.get("HTTP_X_DEVICE_ID") or "").strip()[:200] if request else ""
    return _h(raw) if raw else ""


def record(request, *, username, outcome, user=None, method=""):
    """Write one LoginEvent. Never lets a logging failure break a sign-in."""
    from .models import LoginEvent

    try:
        device = device_of(request)
        new_device = False
        if outcome == LoginEvent.SUCCESS and user is not None:
            new_device = _is_new_device(user, device)
        event = LoginEvent.objects.create(
            user=user,
            tenant_id=getattr(user, "tenant_id", None),
            username=(username or "")[:150],
            ip=client_ip(request) if request else None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:300],
            device=device,
            outcome=outcome,
            method=method,
            new_device=new_device,
        )
        if new_device:
            alert_new_device(user, event)
        return event
    except Exception:
        logger.exception("[login] could not record a %s sign-in for %r", outcome, username)
        return None


def _is_new_device(user, device):
    """
    Whether this account has signed in from this browser before. The first
    sign-in an account ever makes is not "new" -- there is nothing to compare
    it with, and a text saying so would only teach people to ignore them.
    """
    from .models import LoginEvent

    past = LoginEvent.objects.filter(user=user, outcome=LoginEvent.SUCCESS)
    if not past.exists():
        return False
    if not device:
        # An older frontend that sends no id: cannot tell, so do not cry wolf.
        return False
    return not past.filter(device=device).exists()


# ─────────────────────────────────────────────────────────────────── alerts

def _alert_phone(user):
    if user.tenant_id:
        return getattr(user.tenant, "contact_phone", "") or ""
    from .config import get_platform_setting
    return get_platform_setting("SECURITY_ALERT_PHONE", "") or ""


def _send(user, message):
    phone = _alert_phone(user)
    if not phone:
        return
    try:
        # The platform's own sender (tenant=None), as platform billing uses to
        # reach operators: an alert about an operator's account must not
        # depend on, or spend, that operator's SMS credit.
        from .tasks.notification_tasks import notify_customer_task
        notify_customer_task.delay(phone, message, tenant_id=None)
    except Exception:
        logger.exception("[login] could not send a security alert for %s", user.pk)


def alert_new_device(user, event):
    if user.role not in ALERT_ROLES:
        return
    _send(user, (
        f"SmartBill: {user.username} signed in from a new device "
        f"(IP {event.ip or 'unknown'}). If this was not you, change the "
        "password now and use 'Sign out everywhere' in Account settings."
    ))


def alert_mfa_changed(user, *, enabled):
    """
    Two-step switched on or off. Told to the operator whatever the role:
    someone holding a stolen session could switch it on with THEIR phone and
    lock the real owner out, and this is how the owner finds out in time.
    """
    if user.role == "customer":
        return
    state = "turned ON" if enabled else "turned OFF"
    _send(user, (
        f"SmartBill: two-step sign-in was {state} for {user.username}. If this "
        "was not you, contact SmartBill support immediately."
    ))


def alert_locked(username, ip):
    """Tell the account's owner that someone has been guessing at it."""
    from .models import User

    user = User.objects.filter(username__iexact=_norm(username)).select_related("tenant").first()
    if user is None or user.role == User.CUSTOMER:
        return
    _send(user, (
        f"SmartBill: repeated failed sign-ins to {user.username} "
        f"(last from IP {ip or 'unknown'}). The account is paused for 15 "
        "minutes. If this was not you, change the password."
    ))


def failed(request, username, *, outcome=None):
    """A wrong attempt: count it, record it, and alert when it trips a lock."""
    from .models import LoginEvent, User

    ip = client_ip(request) if request else None
    tripped = note_failure(username, ip)
    user = User.objects.filter(username__iexact=_norm(username)).first()
    record(request, username=username, outcome=outcome or LoginEvent.FAILED, user=user)
    if tripped:
        logger.warning("[login] lock tripped for %r from %s", username, ip)
        alert_locked(username, ip)


# ──────────────────────────────────────────────── every authenticate() call

class LockoutModelBackend(ModelBackend):
    """
    Django's own backend, refusing while the username is locked.

    This is what puts the Django admin login behind the same limits as the
    API: it calls authenticate(), and so does everything else. Raising
    PermissionDenied stops Django trying further backends and fires
    user_login_failed, which is where the failure gets counted -- see
    signals below.

    Calls with no request (management commands, internal checks) are left
    alone: there is no address to count against and nobody to stop.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if request is not None and username is not None:
            if locked_for(username, client_ip(request)):
                raise PermissionDenied("locked")
            if malformed(username, password):
                # None, not an exception: authenticate() then fires
                # user_login_failed and it is counted like a wrong password.
                return None
        return super().authenticate(request, username=username, password=password, **kwargs)


def on_login_failed(sender, credentials, request=None, **kwargs):
    """
    user_login_failed: every wrong password through authenticate().

    The API login view counts its own failures before authenticate() runs,
    and marks the request so they are not counted twice.
    """
    if request is None or getattr(request, "_login_counted", False):
        return
    username = credentials.get("username") or ""
    if locked_for(username, client_ip(request)):
        from .models import LoginEvent
        record(request, username=username, outcome=LoginEvent.LOCKED)
        return
    failed(request, username)


def on_admin_logged_in(sender, request, user, **kwargs):
    """user_logged_in fires for the Django admin; the API records its own."""
    if request is None or getattr(request, "_login_counted", False):
        return
    from .models import LoginEvent
    clear_failures(user.username, client_ip(request))
    record(request, username=user.username, outcome=LoginEvent.SUCCESS,
           user=user, method=LoginEvent.ADMIN_SITE)
