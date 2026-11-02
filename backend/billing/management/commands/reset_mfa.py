"""
Turn off two-step sign-in for one account, for someone who has lost their
phone AND their backup codes.

    python manage.py reset_mfa <username>

Run on the server, so it needs shell access to the production box -- which is
the point: there is no web path to switch someone else's second factor off,
because anyone who could reach such a path with a stolen session would use it.

The account's sessions are ended as well. If the account is in a role that
requires two-step, its holder is walked through setting it up again at their
next sign-in.
"""

from django.core.management.base import BaseCommand, CommandError

from billing.models import AdminActionLog, User, record_admin_action


class Command(BaseCommand):
    help = "Turn off two-step sign-in for an account that has lost its authenticator."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **options):
        user = User.objects.filter(username__iexact=username).first()
        if user is None:
            raise CommandError(f"No account called {username!r}.")
        user.mfa_enabled = False
        user.mfa_secret = ""
        user.mfa_last_step = 0
        user.mfa_enabled_at = None
        user.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_last_step", "mfa_enabled_at"])
        user.mfa_backup_codes.all().delete()
        user.invalidate_sessions()
        record_admin_action(None, AdminActionLog.RESET_MFA, target_user=user,
                            detail="manage.py reset_mfa")
        self.stdout.write(self.style.SUCCESS(
            f"Two-step sign-in reset for {user.username}; their sessions have been ended."))
