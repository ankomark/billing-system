"""
Keep each router's busiest moment of the day.

RouterDevice.active_sessions is overwritten by every health probe, so the most
clients a site ever served at once was never recorded anywhere. This table
holds the day's high-water mark per router, for the dashboard to chart beside
daily revenue. See RouterDailyPeak.

The RLS half follows 0057 -- see its notes on why the NULLIF/COALESCE form is
used and why FORCE is needed when Django connects as the table's owner. No-op
on SQLite, which has no RLS.
"""

import billing.models
import django.db.models.deletion
from django.db import migrations, models

TABLES = ["billing_routerdailypeak"]
POLICY = "tenant_isolation"

SCOPE = (
    "tenant_id = COALESCE("
    "NULLIF(current_setting('app.current_tenant_id', true), '')::integer, "
    "tenant_id)"
)

APPLY = """
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS {policy} ON {table};
CREATE POLICY {policy} ON {table}
    USING ({scope})
    WITH CHECK ({scope});
"""

REMOVE = """
DROP POLICY IF EXISTS {policy} ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


def apply_policies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(APPLY.format(table=table, policy=POLICY, scope=SCOPE))


def remove_policies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(REMOVE.format(table=table, policy=POLICY))


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0073_pppoe_usage_state_per_router'),
    ]

    operations = [
        migrations.CreateModel(
            name='RouterDailyPeak',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField()),
                ('peak_sessions', models.PositiveIntegerField()),
                ('peak_at', models.DateTimeField()),
                ('router', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_peaks', to='billing.routerdevice')),
                ('tenant', models.ForeignKey(blank=True, default=billing.models.default_tenant, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='billing.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'day'], name='rpeak_tenant_day_idx')],
                'constraints': [models.UniqueConstraint(fields=('router', 'day'), name='router_daily_peak_uniq')],
            },
        ),
        migrations.RunPython(apply_policies, remove_policies),
    ]
