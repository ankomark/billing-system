from django.core.management.base import BaseCommand

# The legacy billing/tasks_usage module wrote rows without a tenant and would
# have failed once a second operator existed. The Celery task supersedes it.
from billing.tasks.usage_tasks import collect_pppoe_usage_snapshots


class Command(BaseCommand):
    help = "Collect PPPoE usage deltas from MikroTik sessions (all operators)"

    def handle(self, *args, **options):
        processed = collect_pppoe_usage_snapshots()
        self.stdout.write(
            self.style.SUCCESS(f"PPPoE usage collected for {processed} customer(s)")
        )
