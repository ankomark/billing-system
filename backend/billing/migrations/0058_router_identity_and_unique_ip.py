from django.db import migrations, models


def drop_duplicate_addresses(apps, schema_editor):
    """
    Clear the way for the unique constraint below.

    Duplicates are possible in existing data because nothing ever refused them:
    routers were typed into the Django admin, and the same address could be
    entered twice for one operator. Only rows with no customers pointing at them
    are removed — a duplicate that is actually in use is left alone, and the
    migration then fails loudly rather than deciding on its own which of two
    live routers an operator meant to keep.
    """
    RouterDevice = apps.get_model("billing", "RouterDevice")
    Customer = apps.get_model("billing", "Customer")

    seen = set()
    for router in RouterDevice.objects.order_by("tenant_id", "ip_address", "id"):
        key = (router.tenant_id, router.ip_address)
        if key not in seen:
            seen.add(key)
            continue
        if Customer.objects.filter(router_id=router.id).exists():
            continue
        router.delete()


class Migration(migrations.Migration):
    """
    Operators register their own routers now, rather than a platform owner
    typing rows into the Django admin on their behalf. That moves the form into
    the hands of people who have never seen the model, so the two things the
    admin relied on a careful human for have to be enforced here: that an
    address is not registered twice, and that there is some record of which
    physical box a row actually describes.
    """

    dependencies = [
        ("billing", "0057_rls_tetheringcase"),
    ]

    operations = [
        migrations.AddField(
            model_name="routerdevice",
            name="identity",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="routerdevice",
            name="serial_number",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.RunPython(
            drop_duplicate_addresses, migrations.RunPython.noop, elidable=True
        ),
        migrations.AddConstraint(
            model_name="routerdevice",
            constraint=models.UniqueConstraint(
                fields=("tenant", "ip_address"),
                name="routerdevice_unique_ip_per_tenant",
            ),
        ),
    ]
