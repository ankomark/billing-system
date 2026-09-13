"""
Keep a PPPoE subscriber's counters per station, as the hotspot ones now are.

A PPPoE account exists on every router so the subscriber can roam, and a
session's byte counters live on the interface serving it -- starting at zero
there. One pair of counters per subscriber therefore cannot describe two
stations: the second reads as a counter that has gone backwards, which is
indistinguishable from a reboot, so the interval is discarded on every poll.

Latent rather than observed here. There are two PPPoE subscribers on this
estate and neither had a session on more than one router when this was
checked. It is the same fault the hotspot collector was carrying, and leaving
the shape in place behind a fix for its twin is how it comes back.

The backfill claims each existing row for the customer's home router, which is
where those counters were read from, so the first collection after this is an
ordinary delta rather than a re-baseline. A subscriber who appears at another
station gets a fresh row, which starts from that station's counters.
"""

from django.db import migrations, models
import django.db.models.deletion


def claim_rows_for_the_home_router(apps, schema_editor):
    PPPoEUsageState = apps.get_model("billing", "PPPoEUsageState")

    seen = set()
    updates = []
    for state in PPPoEUsageState.objects.select_related("customer").iterator():
        router_id = getattr(state.customer, "router_id", None)
        if router_id is None:
            continue
        key = (state.customer_id, router_id)
        if key in seen:
            continue
        seen.add(key)
        state.router_id = router_id
        updates.append(state)
        if len(updates) >= 500:
            PPPoEUsageState.objects.bulk_update(updates, ["router"])
            updates = []
    if updates:
        PPPoEUsageState.objects.bulk_update(updates, ["router"])


def release_rows(apps, schema_editor):
    PPPoEUsageState = apps.get_model("billing", "PPPoEUsageState")
    PPPoEUsageState.objects.update(router=None)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0072_hotspot_usage_state_per_router"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pppoeusagestate",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pppoe_usage_states",
                to="billing.customer",
            ),
        ),
        migrations.AddField(
            model_name="pppoeusagestate",
            name="router",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pppoe_usage_states",
                to="billing.routerdevice",
                help_text="The station these counters were read from. Null is "
                          "a row written before counters were kept per "
                          "station.",
            ),
        ),
        migrations.RunPython(claim_rows_for_the_home_router, release_rows),
        migrations.AlterUniqueTogether(
            name="pppoeusagestate",
            unique_together={("customer", "router")},
        ),
    ]
