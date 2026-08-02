from django.db import migrations, models


class Migration(migrations.Migration):
    """
    A router is no longer condemned by one missed probe.

    Existing rows start at zero, which is right either way: a router currently
    marked online begins with a clean slate, and one already marked offline
    stays offline until it answers — the counter only decides when the flag
    turns over, not what it is now.
    """

    dependencies = [
        ("billing", "0054_rls_connectionattempt"),
    ]

    operations = [
        migrations.AddField(
            model_name="routerdevice",
            name="consecutive_failures",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
