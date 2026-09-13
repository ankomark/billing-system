"""
Keep a subscriber's counters per ACCOUNT -- per device, per station.

RouterOS meters a hotspot account, which is one MAC on one router. A
subscriber with two phones at two sites therefore has four sets of counters,
each starting at zero and only climbing, and one pair per person cannot
describe any of them.

One pair went wrong twice over. Traffic at a second station looked like a
counter that had gone backwards -- read as a router reboot, re-baselined, and
the interval thrown away, every poll. And the collector matched sessions
against `customer.hotspot_username`, a single MAC, while `mac-auth-mode` names
every session after the device's own address: any subscriber online on a
different handset was invisible to it. 85 of 347 entitled devices were in that
state on 2026-09-13, usage recorded as zero, the data cap never firing from
our side while the router's own limit cut them off.

The backfill is the careful part. Existing rows are claimed by the customer's
home router and their hotspot_username, which is the account those counters
were actually read from, so a subscriber on their usual phone at their usual
station keeps the baseline they had and the first collection after this is an
ordinary delta. Every other account gets a fresh row, which the collector
baselines without recording -- one interval lost, once, instead of a whole
session counter arriving as a single delta.
"""

from django.db import migrations, models
import django.db.models.deletion


def claim_rows_for_the_account_they_came_from(apps, schema_editor):
    HotspotUsageState = apps.get_model("billing", "HotspotUsageState")

    # Only where it does not collide: a customer cannot hold two rows for one
    # account. Nothing can collide here in practice -- both columns were just
    # added -- but the constraint lands next, and a migration that can violate
    # it is not worth the saving.
    seen = set()
    updates = []
    for state in HotspotUsageState.objects.select_related("customer").iterator():
        router_id = getattr(state.customer, "router_id", None)
        mac = (getattr(state.customer, "hotspot_username", "") or "").upper()
        if router_id is None or not mac:
            continue
        key = (state.customer_id, router_id, mac)
        if key in seen:
            continue
        seen.add(key)
        state.router_id = router_id
        state.mac_address = mac
        updates.append(state)
        if len(updates) >= 500:
            HotspotUsageState.objects.bulk_update(
                updates, ["router", "mac_address"])
            updates = []
    if updates:
        HotspotUsageState.objects.bulk_update(
            updates, ["router", "mac_address"])


def release_rows(apps, schema_editor):
    HotspotUsageState = apps.get_model("billing", "HotspotUsageState")
    HotspotUsageState.objects.update(router=None, mac_address="")


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0071_subscription_data_cap"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hotspotusagestate",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hotspot_usage_states",
                to="billing.customer",
            ),
        ),
        migrations.AddField(
            model_name="hotspotusagestate",
            name="router",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hotspot_usage_states",
                to="billing.routerdevice",
                help_text="The station these counters were read from. Null is "
                          "a row written before counters were kept per "
                          "station.",
            ),
        ),
        migrations.AddField(
            model_name="hotspotusagestate",
            name="mac_address",
            field=models.CharField(
                blank=True, default="", max_length=17,
                help_text="The device whose account these counters belong to. "
                          "Blank is a row written before counters were kept "
                          "per device.",
            ),
        ),
        migrations.RunPython(
            claim_rows_for_the_account_they_came_from, release_rows),
        migrations.AlterUniqueTogether(
            name="hotspotusagestate",
            unique_together={("customer", "router", "mac_address")},
        ),
    ]
