"""
Rewrite the RLS policies so an empty setting cannot raise.

The original expression read:

    current_setting(...) IS NULL OR current_setting(...) = ''
    OR tenant_id = current_setting(...)::integer

Postgres does not guarantee that OR short-circuits — the planner may evaluate
any branch — so `''::integer` could be attempted even when the guard ahead of
it was true, failing the query with:

    invalid input syntax for type integer: ""

It never showed up because nothing ever set the value to an empty string: the
middleware did not write the setting at all, so it was always unset and the
IS NULL branch matched. Once requests began writing "" to mean unscoped, every
query against a protected table failed.

The replacement never casts an empty string:

    NULLIF(current_setting(...), '')::integer   -> NULL when unset, no cast
    tenant_id = COALESCE(that, tenant_id)       -> every row when unset

One expression, no reliance on evaluation order.
"""

from django.db import migrations


TABLES = [
    "billing_routerdevice",
    "billing_customer",
    "billing_routerfailoverlog",
    "billing_package",
    "billing_subscription",
    "billing_invoice",
    "billing_voucher",
    "billing_payment",
    "billing_expiryreminderlog",
    "billing_accessauditlog",
    "billing_systemsetting",
    "billing_mpesatransaction",
    "billing_pppoeusagesnapshot",
    "billing_pppoeusagestate",
    "billing_pppoeusagerecord",
    "billing_hotspotusagestate",
    "billing_hotspotusagerecord",
    "billing_usagerecord",
    "billing_tenantsubscription",
    "billing_tenantinvoice",
    "billing_tenantpayment",
]

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


def apply_policies(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(APPLY.format(table=table, policy=POLICY, scope=SCOPE))


def noop(apps, schema_editor):
    """
    Deliberately not reinstated on reverse.

    The previous expression is the bug this migration exists to remove; putting
    it back would break every query as soon as a request set the value.
    """
    return


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0036_impersonation_log"),
    ]

    operations = [
        migrations.RunPython(apply_policies, noop),
    ]
