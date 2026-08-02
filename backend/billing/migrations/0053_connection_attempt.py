import django.db.models.deletion
from django.db import migrations, models

import billing.models


class Migration(migrations.Migration):
    """
    The connections that did not happen.

    Only successes were ever recorded. If twenty people mistyped a code today,
    or a package sold for one device was being passed around a room, the
    operator had no way to know — the customer gives up and the operator hears
    nothing.
    """

    dependencies = [
        ("billing", "0052_protect_package_and_archive"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConnectionAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("code_tried", models.CharField(blank=True, max_length=40)),
                ("mac_address", models.CharField(blank=True, max_length=50)),
                ("outcome", models.CharField(
                    choices=[
                        ("invalid", "Code not recognised"),
                        ("device_limit", "Already on the most devices allowed"),
                        ("blocked", "Device is blocked"),
                        ("no_provider", "Portal could not be identified"),
                    ],
                    max_length=20,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant", models.ForeignKey(
                    blank=True,
                    default=billing.models.default_tenant,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="billing.tenant")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="connectionattempt",
            index=models.Index(fields=["tenant", "created_at"],
                               name="attempt_tenant_created_idx"),
        ),
    ]
