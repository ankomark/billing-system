from django.core.management.base import BaseCommand

# Import from the task module directly. `billing.tasks` is a package, so the
# old `from billing.tasks import send_expiry_reminders` could never resolve.
from billing.tasks.reminder_tasks import send_expiry_reminders


class Command(BaseCommand):
    help = "Send subscription expiry reminders"

    def handle(self, *args, **kwargs):
        # Run the Celery task body synchronously — no broker needed.
        sent = send_expiry_reminders()
        self.stdout.write(
            self.style.SUCCESS(f"Expiry reminders sent: {sent}")
        )
