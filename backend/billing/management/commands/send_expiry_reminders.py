from django.core.management.base import BaseCommand
from billing.tasks import send_expiry_reminders


class Command(BaseCommand):
    help = "Send subscription expiry reminders"

    def handle(self, *args, **kwargs):
        send_expiry_reminders()
        self.stdout.write(
            self.style.SUCCESS("✅ Expiry reminders sent successfully")
        )
