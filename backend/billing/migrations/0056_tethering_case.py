import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import billing.models


class Migration(migrations.Migration):
    """
    Cases of one paid connection feeding a second network.

    A subscriber sharing their access over their phone's own hotspot is
    invisible to everything else here: the router sees one MAC and one session,
    so the device limit and shared-users — which stop a code being typed into
    four phones — say nothing about what is behind the one phone that used it.

    Detection is a hop-count mismatch, which is evidence and not proof, so a
    case accumulates sightings across sweeps and only a sustained one is acted
    on. See billing/services/tethering.py.
    """

    dependencies = [
        ("billing", "0055_router_consecutive_failures"),
    ]

    operations = [
        migrations.CreateModel(
            name="TetheringCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("ip_address", models.CharField(blank=True, max_length=45)),
                ("mac_address", models.CharField(blank=True, max_length=50)),
                ("hops", models.PositiveSmallIntegerField(default=1)),
                ("mixed_ttl", models.BooleanField(default=False)),
                ("high_connections", models.BooleanField(default=False)),
                ("throttled_ip", models.CharField(blank=True, max_length=45)),
                ("observations", models.PositiveIntegerField(default=0)),
                ("acted_observations", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(
                    choices=[("watching", "Watching"), ("warned", "Warned"),
                             ("throttled", "Throttled"),
                             ("kicked", "Session ended"), ("cleared", "Cleared")],
                    default="watching", max_length=12)),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(
                    default=django.utils.timezone.now)),
                ("acted_at", models.DateTimeField(blank=True, null=True)),
                ("notified_at", models.DateTimeField(blank=True, null=True)),
                ("cleared_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.CharField(blank=True, max_length=255)),
                # Nullable: a suspect address that cannot be tied to a
                # subscriber is still worth showing an operator, and is never
                # acted on precisely because there is nobody behind it.
                ("customer", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="tethering_cases", to="billing.customer")),
                ("router", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="tethering_cases", to="billing.routerdevice")),
                # As TenantScopedModel declares it: PROTECT so removing an
                # operator never silently destroys history, blank=True so DRF
                # does not mark it required on "__all__" serializers.
                ("tenant", models.ForeignKey(
                    blank=True,
                    default=billing.models.default_tenant,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="billing.tenant")),
            ],
            options={"ordering": ["-last_seen"]},
        ),
        migrations.AddIndex(
            model_name="tetheringcase",
            index=models.Index(fields=["tenant", "status"],
                               name="tether_tenant_status_idx"),
        ),
        migrations.AddIndex(
            model_name="tetheringcase",
            index=models.Index(fields=["tenant", "-last_seen"],
                               name="tether_tenant_seen_idx"),
        ),
        migrations.AddConstraint(
            model_name="tetheringcase",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    status__in=("watching", "warned", "throttled", "kicked")),
                fields=("tenant", "customer"),
                name="tethering_one_open_case_per_customer",
            ),
        ),
    ]
