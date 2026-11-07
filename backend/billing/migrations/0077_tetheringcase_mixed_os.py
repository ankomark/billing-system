from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0076_usage_tenant_period_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="tetheringcase",
            name="mixed_os",
            field=models.BooleanField(default=False),
        ),
    ]
