"""
Backfill the device table from the MAC already bound on each customer.

Without this, every subscriber who is already bound has hotspot_username set
and no device rows — and the new limit counts rows. On the day this deployed,
every voucher in the field would have become good for one more phone than it
was sold for. The existing single-device test caught it, which is what that
test is for.

Idempotent: get_or_create keyed on the MAC, which is unique per operator.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Customer = apps.get_model("billing", "Customer")
    CustomerDevice = apps.get_model("billing", "CustomerDevice")

    bound = (
        Customer.objects.exclude(hotspot_username="")
        .exclude(hotspot_username=None)
        .values_list("id", "tenant_id", "hotspot_username")
    )
    for customer_id, tenant_id, mac in bound.iterator(chunk_size=500):
        CustomerDevice.objects.get_or_create(
            tenant_id=tenant_id,
            mac_address=mac,
            defaults={"customer_id": customer_id},
        )


def unbackfill(apps, schema_editor):
    """
    Only the rows this created, identified by matching the customer's own
    bound MAC. A device added since is somebody's second phone and is not this
    migration's to remove.
    """
    Customer = apps.get_model("billing", "Customer")
    CustomerDevice = apps.get_model("billing", "CustomerDevice")

    for customer_id, tenant_id, mac in (
        Customer.objects.exclude(hotspot_username="")
        .exclude(hotspot_username=None)
        .values_list("id", "tenant_id", "hotspot_username")
        .iterator(chunk_size=500)
    ):
        CustomerDevice.objects.filter(
            tenant_id=tenant_id, customer_id=customer_id, mac_address=mac
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0049_rls_customerdevice"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
