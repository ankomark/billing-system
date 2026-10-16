"""
Dump what a real OLT answers, to finish its vendor profile against the box.

    python manage.py fiber_discover --olt 1 --out /tmp/vsol.json
    python manage.py fiber_discover --olt 2 --cli "show onu info" --cli "show onu optical-info"

Read-only: SNMP walks, and CLI commands the telnet client accepts (show/display
only). The output is the raw material for profile_overrides -- which
enterprise OID carries ONU receive power, distance, down reason -- so a column
is set from evidence instead of guessed.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from billing.tenancy import all_tenants, tenant_context
from fiber.drivers import OltError, get_driver
from fiber.drivers.telnet import TelnetCli
from fiber.models import Olt


class Command(BaseCommand):
    help = "Walk an OLT's SNMP tree (and optionally run show commands) and save it as JSON."

    def add_arguments(self, parser):
        parser.add_argument("--olt", type=int, required=True)
        parser.add_argument("--out", default="")
        parser.add_argument("--cli", action="append", default=[], help="a show command to capture")

    def handle(self, *args, **opts):
        with all_tenants():
            olt = Olt.objects.all_tenants().filter(pk=opts["olt"]).first()
        if olt is None:
            raise CommandError("no such OLT")
        result = {"olt": olt.name, "vendor": olt.vendor}
        with tenant_context(olt.tenant_id):
            try:
                result["snmp"] = get_driver(olt).discover()
            except OltError as exc:
                result["snmp_error"] = str(exc)
            if opts["cli"]:
                try:
                    with TelnetCli(olt.mgmt_ip, olt.cli_port, olt.cli_username, olt.cli_password) as t:
                        result["cli"] = {cmd: t.run(cmd) for cmd in opts["cli"]}
                except OltError as exc:
                    result["cli_error"] = str(exc)
        text = json.dumps(result, indent=1, default=str)
        if opts["out"]:
            with open(opts["out"], "w") as fh:
                fh.write(text)
            self.stdout.write(f"wrote {len(text)} bytes to {opts['out']}")
        else:
            self.stdout.write(text)
