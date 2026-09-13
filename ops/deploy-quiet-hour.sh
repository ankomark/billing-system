#!/bin/bash
#
# Scheduled deploy, run from an `at` job at the quiet hour.
#
# Not a bare call to deploy.sh. Two things have to happen around it:
# the Celery queue must be empty before the worker restarts, because
# CELERY_TASK_ACKS_LATE is off and a task still running when the worker
# stops is lost outright -- enable_customer_task is one of those, and
# losing it means a paying customer is not provisioned. And the result
# has to be checked, because nobody is watching this run.
#
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

STAMP=$(date -u +%Y%m%d-%H%M%S)
LOG=/home/deploy/deploy-scheduled-$STAMP.log
exec >>"$LOG" 2>&1

say() { echo "[$(date -u '+%F %T')Z] $*"; }

say "=== scheduled deploy starting ($(TZ=Africa/Nairobi date '+%F %T') EAT) ==="

cd /home/deploy/billing/backend || { say "FATAL: cannot enter backend dir"; exit 1; }

say "--- current state ---"
say "deployed commit: $(cd /home/deploy/billing && git rev-parse --short HEAD)"
docker compose ps --format "table {{.Service}}\t{{.Status}}"

# ---- gate: the queue must be empty ------------------------------------
say "--- waiting for the Celery queue to drain (up to 10 minutes) ---"
drained=0
for i in $(seq 1 60); do
    depth=$(docker compose exec -T redis redis-cli LLEN celery 2>/dev/null | tr -d '\r\n')
    active=$(docker compose exec -T worker celery -A config inspect --json active --timeout 8 2>/dev/null | grep -o '"id"' | wc -l)
    say "  check $i: pending=${depth:-unknown} active=${active:-unknown}"
    if [ "${depth:-1}" = "0" ] && [ "${active:-1}" = "0" ]; then
        drained=1
        break
    fi
    sleep 10
done

if [ "$drained" != "1" ]; then
    say "ABORTED: the queue never drained, so a restart would lose work in flight."
    say "Nothing was deployed. The tree is untouched and the running code is unchanged."
    exit 1
fi
say "queue is empty -- proceeding"

# ---- the deploy itself -------------------------------------------------
say "--- running /home/deploy/deploy.sh ---"
if /home/deploy/deploy.sh; then
    say "deploy.sh exited 0"
else
    rc=$?
    say "FAILED: deploy.sh exited $rc"
    say "--- state after the failure ---"
    docker compose ps --format "table {{.Service}}\t{{.Status}}"
    exit $rc
fi

# ---- verify ------------------------------------------------------------
say "--- verifying ---"
say "deployed commit is now: $(cd /home/deploy/billing && git rev-parse --short HEAD)"
docker compose ps --format "table {{.Service}}\t{{.Status}}"

sleep 10
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://api.smartbillsolution.com/api/ 2>/dev/null)
say "api.smartbillsolution.com/api/ -> $code (401 or 200 both mean the app is answering)"

say "--- errors in the first minute after restart ---"
docker compose logs --since 2m web worker 2>&1 | grep -iE "traceback|internal server error|critical" | grep -viE "whatsapp|sms" | tail -15
say "(nothing above means a clean start)"

say "=== scheduled deploy finished ==="
