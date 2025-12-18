from django.core.management.base import BaseCommand
from billing.tasks_usage_hotspot import collect_hotspot_usage


class Command(BaseCommand):
    help = "Collect Hotspot usage deltas"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=300)

    def handle(self, *args, **options):
        collect_hotspot_usage(interval_seconds=options["interval"])
        self.stdout.write(self.style.SUCCESS("Hotspot usage collected"))
