#!/bin/bash
# Nightly encrypted database dump. See GUIDE.md 1.7.
set -euo pipefail

STAMP=$(date +%Y%m%d-%H%M)
OUT=/tmp/billing-$STAMP.sql.gz
LOCAL=/home/deploy/backups

cd /home/deploy/billing/backend

# Dumped as wifi_admin, the superuser, DELIBERATELY.
#
# wifi_app is NOBYPASSRLS, so every tenant_isolation policy applies to it --
# including to the SELECTs pg_dump issues. A dump taken as the app role omits
# every tenant-scoped row, succeeds, exits 0, and produces a file that looks
# plausible until the day you restore it and find the customers missing.
docker compose exec -T postgres pg_dump -U wifi_admin -d wifi_billing | gzip > "$OUT"

# An empty dump is the failure this whole script exists to survive, and it is
# silent by nature: gzip of nothing is still a valid gzip. Refuse to ship one.
SIZE=$(stat -c%s "$OUT")
if [ "$SIZE" -lt 10000 ]; then
    echo "ABORT: dump is only ${SIZE} bytes - refusing to overwrite good backups" >&2
    rm -f "$OUT"
    exit 1
fi

gpg --batch --yes --passphrase-file /home/deploy/.backup-pass \
    --symmetric --cipher-algo AES256 "$OUT"

if rclone listremotes 2>/dev/null | grep -q .; then
    REMOTE=$(rclone listremotes | head -1)
    rclone copy "$OUT.gpg" "${REMOTE}billing-backups/"
    rclone delete --min-age 30d "${REMOTE}billing-backups/" || true
    echo "$(date -Is) off-site OK (${SIZE} bytes raw) -> ${REMOTE}"
else
    cp "$OUT.gpg" "$LOCAL/"
    find "$LOCAL" -name '*.gpg' -mtime +30 -delete
    echo "$(date -Is) LOCAL ONLY (${SIZE} bytes raw) - no rclone remote configured"
fi

rm -f "$OUT" "$OUT.gpg"
