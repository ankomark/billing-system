"""
The Django admin's sign-in, with the second step added.

The admin is a platform-owner tool that reaches every operator's data
directly, and it had a username and password form with no limit on attempts.
The attempt limit now comes from LockoutModelBackend; this adds the code.
"""

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError

from . import login_security as ls
from . import mfa
from .models import LoginEvent


class MFAAdminAuthenticationForm(AdminAuthenticationForm):
    otp = forms.CharField(
        label="Authenticator code",
        required=False,
        widget=forms.TextInput(attrs={
            "autocomplete": "one-time-code", "inputmode": "numeric",
            "placeholder": "6-digit code or backup code",
        }),
    )

    def clean(self):
        cleaned = super().clean()   # the password, the lock, and is_staff
        user = self.get_user()
        if user is None:
            return cleaned
        if not user.mfa_enabled:
            if mfa.is_required(user):
                raise ValidationError(
                    "Set up two-step sign-in from the dashboard (Account → "
                    "Security) before using the admin.",
                    code="mfa_setup_required",
                )
            return cleaned
        if mfa.check_code(user, cleaned.get("otp")) is None:
            ls.failed(self.request, user.username, outcome=LoginEvent.MFA_FAILED)
            raise ValidationError(
                "Enter the current code from your authenticator app, or a "
                "backup code.",
                code="mfa_invalid",
            )
        return cleaned
