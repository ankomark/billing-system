from django.core.management.base import BaseCommand

from billing.tasks.usage_tasks import collect_hotspot_usage_snapshots


class Command(BaseCommand):
    help = "Collect Hotspot usage deltas from MikroTik sessions (all operators)"

    def handle(self, *args, **options):
        processed = collect_hotspot_usage_snapshots()
        self.stdout.write(
            self.style.SUCCESS(f"Hotspot usage collected for {processed} customer(s)")
        )
