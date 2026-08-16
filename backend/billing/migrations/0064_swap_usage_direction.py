"""
Put download and upload back the right way round in the stored usage.

The collector wrote the router's rx into download_bytes and its tx into upload
from the first commit. rx is what the router *received*, which is what the
subscriber *sent* — so every row on the platform has had the two reversed.

It hid because nothing that matters reads them apart. usage_since() adds both
into one number, so data caps and every enforcement decision have always been
correct; the fault reaches only the labels on the graphs and the two figures on
the subscriber's own portal.

Production before this ran: 23,009 hotspot rows totalling 63GB of "download"
against 718GB of "upload", with the labelled upload larger on 21,382 of them.
Eleven times more sent than received by every subscriber at once, which is not
a thing a WiFi network does.

Swapping the columns is lossless and exactly reverses the error — the numbers
are right, only the two names on them were exchanged. Row totals do not move,
so no cap decision changes, retrospectively or otherwise.

Done in SQL rather than the ORM because a plain UPDATE reads every right-hand
side from the row as it was before the statement, which swaps the pair in one
pass over 23k rows instead of loading them.

RLS: the policy from 0037 reads
`tenant_id = COALESCE(NULLIF(current_setting('app.current_tenant_id',''),...))`,
which matches every row when nothing has set the scope. A migration sets
nothing, so this reaches all operators. The count is checked and logged
afterwards, because an RLS-silenced UPDATE reports success having touched
nothing.

Reversible with itself: swapping twice is the identity. That is deliberate —
if this turns out to be the wrong call, `migrate billing 0063` puts the data
back exactly as it was.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

# (table, first column, second column)
SWAPS = [
    ("billing_hotspotusagerecord", "download_bytes", "upload_bytes"),
    ("billing_pppoeusagerecord", "download_bytes", "upload_bytes"),
    # The daily rollup, whose rx_bytes/tx_bytes are sums of the two above and
    # so carry the same reversal. Named for the router as the raw rows are.
    ("billing_usagerecord", "rx_bytes", "tx_bytes"),
]


def swap(apps, schema_editor):
    """
    Exchange the two columns in place, on every operator's rows.

    Not idempotent, and must not be: running it twice puts the fault back.
    Django's migration table is what guarantees it runs once.
    """
    with schema_editor.connection.cursor() as cur:
        for table, first, second in SWAPS:
            cur.execute(
                f"UPDATE {table} SET {first} = {second}, {second} = {first}"
            )
            logger.info("[0064] swapped %s rows in %s", cur.rowcount, table)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0063_alter_connectionattempt_outcome"),
    ]

    operations = [
        # Its own reverse: the swap is an involution.
        migrations.RunPython(swap, swap),
    ]
