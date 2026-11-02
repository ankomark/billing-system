"""
Password rules that know who the password is for.

Django's MinimumLengthValidator applies one length to every account. Here that
is wrong in both directions: the people running a business -- with M-Pesa
credentials, every subscriber's details and the routers behind them -- were
held to eight characters, and raising it for everyone would also land on
subscribers' portal logins, which are a different risk entirely.
"""

import hashlib
import logging

from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

STAFF_MIN = 12
OTHER_MIN = 8


class RoleMinimumLengthValidator:
    """
    Twelve characters for every account except a subscriber's.

    With no user to go by -- a form that has not created the account yet --
    the stricter rule applies: the only accounts made that way here are
    staff ones.
    """

    def _minimum(self, user):
        role = getattr(user, "role", None)
        return OTHER_MIN if role == "customer" else STAFF_MIN

    def validate(self, password, user=None):
        minimum = self._minimum(user)
        if len(password or "") < minimum:
            raise ValidationError(
                f"This password is too short. It must contain at least {minimum} characters.",
                code="password_too_short",
                params={"min_length": minimum},
            )

    def get_help_text(self):
        return f"Your password must contain at least {STAFF_MIN} characters."


class BreachedPasswordValidator:
    """
    Refuses passwords already published in data breaches.

    A password in those lists is the first thing an attacker tries, however
    long or clever it looks. Checked against Have I Been Pwned by k-anonymity:
    only the first five hex characters of the SHA-1 leave this server, and the
    match is made here against the few hundred suffixes that come back.

    Fails open. A sign-up must not be blocked because a third-party service is
    slow, and the other rules still apply.
    """

    URL = "https://api.pwnedpasswords.com/range/"

    def validate(self, password, user=None):
        if not getattr(settings, "PASSWORD_BREACH_CHECK", False) or not password:
            return
        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix, suffix = digest[:5], digest[5:]
        try:
            import requests
            resp = requests.get(self.URL + prefix, timeout=3,
                                headers={"Add-Padding": "true"})
            resp.raise_for_status()
        except Exception:
            logger.warning("[password] breach check unavailable; skipped")
            return
        for line in resp.text.splitlines():
            candidate, _, count = line.partition(":")
            if candidate.strip() == suffix and count.strip() not in ("", "0"):
                raise ValidationError(
                    "This password has appeared in a data breach, so attackers "
                    "already try it. Please choose a different one.",
                    code="password_breached",
                )

    def get_help_text(self):
        return "Your password must not be one that has appeared in a data breach."
