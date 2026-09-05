"""
Caps in megabytes, and a place to record a cap cut-off.

The old fields were a whole number of gigabytes, so the smallest cap anyone
could express was 1 GB and everything under it landed on 0 — which those same
fields documented as unlimited. Operators selling 300 MB and 500 MB bundles
were therefore selling capped packages that were provisioned uncapped.

Existing values are converted rather than dropped: 5 GB becomes 5120 MB, and
0 stays 0 because unlimited is unlimited in either unit.
"""

from django.db import migrations, models


def gb_to_mb(apps, schema_editor):
    Package = apps.get_model("billing", "Package")
    Customer = apps.get_model("billing", "Customer")

    # F() rather than a Python loop: this is one UPDATE per table on a column
    # that exists on every row, and pulling every package and customer through
    # Django to multiply an integer would make a long migration out of a short
    # one.
    Package.objects.all().update(data_cap_mb=models.F("monthly_data_cap_gb") * 1024)
    Customer.objects.filter(custom_data_cap_gb__isnull=False).update(
        custom_data_cap_mb=models.F("custom_data_cap_gb") * 1024
    )


def mb_to_gb(apps, schema_editor):
    """
    Reverse, lossily and knowingly.

    A 300 MB cap has no whole-gigabyte spelling, so going back rounds it *up*
    to 1 GB rather than down to 0. Down would restore the exact bug this
    migration exists to fix — a cap that silently means unlimited — and if a
    rollback has to lose precision it should lose it in the direction that
    keeps the subscriber connected.
    """
    Package = apps.get_model("billing", "Package")
    Customer = apps.get_model("billing", "Customer")

    for pkg in Package.objects.all().iterator():
        pkg.monthly_data_cap_gb = -(-(pkg.data_cap_mb or 0) // 1024)
        pkg.save(update_fields=["monthly_data_cap_gb"])

    for cust in Customer.objects.filter(custom_data_cap_mb__isnull=False).iterator():
        cust.custom_data_cap_gb = -(-(cust.custom_data_cap_mb or 0) // 1024)
        cust.save(update_fields=["custom_data_cap_gb"])


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0067_routerdevice_public_token"),
    ]

    operations = [
        # Added, backfilled, and only then is the old column dropped, so the
        # data conversion never runs against a column that is already gone.
        migrations.AddField(
            model_name="package",
            name="data_cap_mb",
            field=models.PositiveIntegerField(
                default=0, help_text="Data cap in MB. 0 = unlimited."
            ),
        ),
        migrations.AddField(
            model_name="customer",
            name="custom_data_cap_mb",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subscription",
            name="capped_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(gb_to_mb, mb_to_gb),
        migrations.RemoveField(model_name="package", name="monthly_data_cap_gb"),
        migrations.RemoveField(model_name="customer", name="custom_data_cap_gb"),
    ]
