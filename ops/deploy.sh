#!/bin/bash
# Pull the latest code and roll it out. Run from anywhere: /home/deploy/deploy.sh
set -euo pipefail

cd /home/deploy/billing

echo "=== 1/5 backup first ==="
# A deploy that applies migrations changes the database shape. If one goes
# wrong there is no undo, so the dump has to exist BEFORE the migration runs,
# not on tonight's schedule.
/home/deploy/backup.sh

echo "=== 2/5 pull ==="
BEFORE=$(git rev-parse --short HEAD)
git pull --ff-only origin main
AFTER=$(git rev-parse --short HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
    echo "already at $AFTER - nothing to deploy"
    exit 0
fi
echo "$BEFORE -> $AFTER"

cd backend

echo "=== 3/5 build ==="
docker compose build

echo "=== 4/5 migrate BEFORE restarting web ==="
# Deliberately its own step rather than relying on `up` to re-run the one-shot
# migrate service: `up` may leave an already-exited container alone, so a
# deploy would silently skip the migration and start new code against an old
# schema. Running it here also means the schema is ready before any request
# reaches the new code, rather than racing it.
docker compose run --rm migrate

echo "=== 5/5 roll out ==="
docker compose up -d --wait

echo "=== state ==="
docker compose ps --format "table {{.Service}}\t{{.Status}}"
curl -sf http://localhost:8000/health/ && echo ""
