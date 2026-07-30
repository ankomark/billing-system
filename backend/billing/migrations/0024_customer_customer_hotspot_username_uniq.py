from django.db import migrations, models
from django.db.models import Count, Max
from django.utils import timezone


def release_duplicate_hotspot_bindings(apps, schema_editor):
    """
    Clear duplicate hotspot MAC bindings so the unique constraint can be applied.

    Nothing previously stopped two customers holding the same hotspot_username,
    and the public status/reconnect endpoints resolved a subscriber with
    .filter(hotspot_username=mac).first() — returning an arbitrary one of them.

    For each contested MAC we keep exactly one holder and blank the rest:
      1. a customer with a live subscription wins (latest expiry, if several)
      2. otherwise the most recently created customer wins

    Losing a binding does not disconnect anyone. The MikroTik hotspot user is
    keyed by MAC and is left untouched; the customer simply re-binds next time
    they validate a voucher.
    """
    Customer = apps.get_model("billing", "Customer")
    Subscription = apps.get_model("billing", "Subscription")
    now = timezone.now()

    contested = (
        Customer.objects
        .exclude(hotspot_username="")
        .values("hotspot_username")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("hotspot_username", flat=True)
    )
    contested = list(contested)

    if not contested:
        return

    released = 0
    for mac in contested:
        holders = list(
            Customer.objects.filter(hotspot_username=mac).order_by("-created_at", "-id")
        )

        live = (
            Subscription.objects
            .filter(customer__in=holders, status="active", expiry_date__gt=now)
            .values("customer_id")
            .annotate(latest=Max("expiry_date"))
            .order_by("-latest")
        )
        live_ids = [row["customer_id"] for row in live]

        if live_ids:
            winner_id = live_ids[0]
        else:
            winner_id = holders[0].id

        loser_ids = [c.id for c in holders if c.id != winner_id]
        if loser_ids:
            # queryset .update() bypasses Customer.save()/full_clean(), which is
            # what we want on historical rows that may fail current validation
            Customer.objects.filter(id__in=loser_ids).update(hotspot_username="")
            released += len(loser_ids)

    print(
        f"\n  Released {released} duplicate hotspot MAC binding(s) "
        f"across {len(contested)} device(s) before applying uniqueness."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0023_add_performance_indexes'),
    ]

    operations = [
        # Must run before AddConstraint — otherwise the constraint fails to
        # build on any database that already contains duplicates.
        migrations.RunPython(
            release_duplicate_hotspot_bindings,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(
                condition=models.Q(('hotspot_username', ''), _negated=True),
                fields=('hotspot_username',),
                name='customer_hotspot_username_uniq',
            ),
        ),
    ]
