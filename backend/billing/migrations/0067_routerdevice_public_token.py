"""
A token that lets a router's own portal say which router it is.

The captive portal carries the operator's token and nothing else, so a
walk-up subscriber could be provisioned onto any of that operator's routers —
selection ties at zero PPPoE sessions across a hotspot estate and the winner
is arbitrary. Someone at one site got a connection configured at another.

Backfilled rather than left null for existing rows, so an operator who
re-uploads config.js to a router they already run gets the same behaviour as
a new one without touching the database.
"""

import secrets

from django.db import migrations, models


def give_every_router_a_token(apps, schema_editor):
    RouterDevice = apps.get_model("billing", "RouterDevice")
    # Model methods are not available on a historical model, so the token is
    # generated here the same way Tenant.save does it.
    for router in RouterDevice.objects.filter(public_token__isnull=True):
        router.public_token = secrets.token_urlsafe(24)[:32]
        router.save(update_fields=["public_token"])


def drop_tokens(apps, schema_editor):
    RouterDevice = apps.get_model("billing", "RouterDevice")
    RouterDevice.objects.update(public_token=None)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0066_customerdevice_subscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="routerdevice",
            name="public_token",
            field=models.CharField(
                blank=True, db_index=True, max_length=32, null=True,
                unique=True),
        ),
        migrations.RunPython(give_every_router_a_token, drop_tokens),
    ]
