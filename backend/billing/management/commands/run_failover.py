from django.core.management.base import BaseCommand
from billing.tasks import run_failover_cycle


class Command(BaseCommand):
    help = "Run router health check and auto failover"

    def handle(self, *args, **kwargs):
        run_failover_cycle()
        self.stdout.write(self.style.SUCCESS("Failover cycle completed"))
