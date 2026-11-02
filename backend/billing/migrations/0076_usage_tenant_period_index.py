# Operator-wide usage, by time. See the Meta comment on PPPoEUsageRecord.
#
# Built CONCURRENTLY on PostgreSQL: the hotspot table is 2.75M rows / 506 MB,
# and a plain CREATE INDEX would hold off the usage collector's inserts for
# the whole build. CONCURRENTLY cannot run inside a transaction, hence
# atomic = False. Other databases (the SQLite used locally) get the ordinary
# AddIndex. Django's own state is updated the same way either way.

from django.db import migrations, models


HS = models.Index(fields=["tenant", "period_start"],
                  include=("download_bytes", "upload_bytes"),
                  name="hsusage_tenant_period_idx")
PP = models.Index(fields=["tenant", "period_start"],
                  include=("download_bytes", "upload_bytes"),
                  name="ppusage_tenant_period_idx")


class AddIndexConcurrentlyWherePossible(migrations.AddIndex):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        if schema_editor.connection.vendor == "postgresql":
            table = model._meta.db_table
            cols = ", ".join(f'"{model._meta.get_field(f).column}"' for f in self.index.fields)
            include = ", ".join(f'"{model._meta.get_field(f).column}"' for f in self.index.include)
            schema_editor.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{self.index.name}" '
                f'ON "{table}" ({cols}) INCLUDE ({include})'
            )
        else:
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{self.index.name}"')
        else:
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('billing', '0075_login_security_and_mfa'),
    ]

    operations = [
        AddIndexConcurrentlyWherePossible(model_name="hotspotusagerecord", index=HS),
        AddIndexConcurrentlyWherePossible(model_name="pppoeusagerecord", index=PP),
    ]
