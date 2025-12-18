from django.core.management.base import BaseCommand
from billing.tasks_usage import collect_pppoe_usage


class Command(BaseCommand):
    help = "Collect PPPoE usage deltas from MikroTik sessions"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=300)

    def handle(self, *args, **options):
        collect_pppoe_usage(interval_seconds=options["interval"])
        self.stdout.write(self.style.SUCCESS("PPPoE usage collection complete"))
