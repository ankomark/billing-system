from django.core.management.base import BaseCommand, CommandError

from billing.tenancy import all_tenants
from fiber.models import Olt
from fiber.services.collector import poll_olt


class Command(BaseCommand):
    help = "Poll one OLT (or all) now and print what came back."

    def add_arguments(self, parser):
        parser.add_argument("--olt", type=int, help="OLT id; default every active OLT")

    def handle(self, *args, **opts):
        with all_tenants():
            qs = Olt.objects.all_tenants().filter(is_active=True)
            if opts.get("olt"):
                qs = qs.filter(pk=opts["olt"])
            olts = list(qs)
        if not olts:
            raise CommandError("no active OLT matched")
        for olt in olts:
            out = poll_olt(olt)
            self.stdout.write(f"{olt.name}: {out}")
