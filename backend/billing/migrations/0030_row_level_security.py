"""
Postgres Row-Level Security: the backstop under the application-layer scoping.

If a query somewhere forgets its tenant filter, this makes the database refuse
the rows anyway. Two details decide whether it does anything at all:

  FORCE ROW LEVEL SECURITY
      Django connects as the role that owns these tables, and an owner bypasses
      RLS unless FORCE is set. Without it you get the appearance of protection
      and none of the substance.

  Transaction-local session variable
      The policy reads app.current_tenant_id. tenancy.tenant_context() sets it
      with set_config(..., true) — transaction-local — because CONN_MAX_AGE
      keeps connections alive across requests and a plain SET would leak one
      operator's context into the next request.

Empty context means unscoped: platform staff and cross-operator sweeps read
everything. That is deliberate, and is why the application layer must still
scope correctly. RLS is the second lock, not the only one.

No-op on SQLite, which has no RLS — so settings_local and the test suite are
unaffected. There is a Postgres-only test that asserts the policy really bites.
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
]

POLICY = "tenant_isolation"

ENABLE_SQL = """
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS {policy} ON {table};
CREATE POLICY {policy} ON {table}
    USING (
        current_setting('app.current_tenant_id', true) IS NULL
        OR current_setting('app.current_tenant_id', true) = ''
        OR tenant_id = current_setting('app.current_tenant_id', true)::integer
    )
    WITH CHECK (
        current_setting('app.current_tenant_id', true) IS NULL
        OR current_setting('app.current_tenant_id', true) = ''
        OR tenant_id = current_setting('app.current_tenant_id', true)::integer
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS {policy} ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


def apply_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(ENABLE_SQL.format(table=table, policy=POLICY))


def remove_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cur:
        for table in TABLES:
            cur.execute(DISABLE_SQL.format(table=table, policy=POLICY))


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0029_tenant_leading_indexes"),
    ]

    operations = [
        migrations.RunPython(apply_rls, remove_rls),
    ]
