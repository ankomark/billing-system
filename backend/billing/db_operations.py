"""
Migration operations that build indexes without locking the table on Postgres.

A plain CREATE INDEX takes an ACCESS EXCLUSIVE lock for the duration of the
build, which stalls every query against that table. On the customer, payment
and mpesatransaction tables that means a visible outage while an index is
rebuilt.

CREATE INDEX CONCURRENTLY avoids the lock but cannot run inside a transaction
block, so any migration using these must set `atomic = False`.

SQLite — used by settings_local and the test suite — has no CONCURRENTLY, so
these fall back to the ordinary path there.
"""

from django.db import migrations


def _is_postgres(schema_editor):
    return schema_editor.connection.vendor == "postgresql"


class AddIndexConcurrentlyIfPostgres(migrations.AddIndex):
    """CREATE INDEX CONCURRENTLY on Postgres, plain CREATE INDEX elsewhere."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        if _is_postgres(schema_editor):
            schema_editor.add_index(model, self.index, concurrently=True)
        else:
            schema_editor.add_index(model, self.index)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        if _is_postgres(schema_editor):
            schema_editor.remove_index(model, self.index, concurrently=True)
        else:
            schema_editor.remove_index(model, self.index)


class RemoveIndexConcurrentlyIfPostgres(migrations.RemoveIndex):
    """DROP INDEX CONCURRENTLY on Postgres, plain DROP INDEX elsewhere."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        index = from_state.models[app_label, self.model_name.lower()].get_index_by_name(self.name)
        if _is_postgres(schema_editor):
            schema_editor.remove_index(model, index, concurrently=True)
        else:
            schema_editor.remove_index(model, index)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        index = to_state.models[app_label, self.model_name.lower()].get_index_by_name(self.name)
        if _is_postgres(schema_editor):
            schema_editor.add_index(model, index, concurrently=True)
        else:
            schema_editor.add_index(model, index)
