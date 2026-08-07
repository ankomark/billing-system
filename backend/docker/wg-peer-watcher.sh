#!/bin/bash
#
# Apply tunnel peer requests written by the platform.
#
# Runs on the HOST as root, not in a container. The web container writes a
# small JSON request into the spool directory and this picks it up; that way
# Django never needs NET_ADMIN, never needs the host's network namespace, and
# never holds an SSH key. An RCE in the web process can ask for a tunnel peer —
# which it is already entitled to do — and cannot touch the server's
# networking.
#
# Install (once, as root):
#
#   install -m 0755 wg-peer-watcher.sh /usr/local/bin/wg-peer-watcher
#   install -d -m 0770 -o deploy -g deploy /var/spool/wg-requests
#   cp wg-peer-watcher.path wg-peer-watcher.service /etc/systemd/system/
#   systemctl daemon-reload
#   systemctl enable --now wg-peer-watcher.path
#
# The spool directory is bind-mounted into the web container read-write; the
# container never reads anything outside it.
#
# EVERY FIELD IS VALIDATED HERE. The platform validates too, but a request file
# is attacker-controlled input the moment anything upstream is compromised, and
# the value of running unprivileged over there is lost if this trusts it.

set -euo pipefail

SPOOL="${WG_SPOOL_DIR:-/var/spool/wg-requests}"
ADD_PEER="${WG_ADD_PEER:-/home/deploy/wg-add-peer.sh}"
SUBNET_PREFIX="${WG_SUBNET_PREFIX:-10.10.0.}"
PYTHON="${WG_PYTHON:-python3}"

# systemd captures stdout into the journal already, so this is just echo —
# piping through `logger` as well would duplicate every line, and would abort
# the run under `set -e` on a system where util-linux is not installed.
#
#   journalctl -u wg-peer-watcher -f
log() { echo "$*"; }

# Refuse anything that is not exactly a base64 WireGuard key. This is the value
# that reaches a command line, so it gets the strictest check: 43 characters of
# the base64 alphabet followed by '='.
valid_key() {
    [[ "$1" =~ ^[A-Za-z0-9+/]{43}=$ ]]
}

# An address inside the tunnel subnet, and nothing else. Not a hostname, not a
# range, not a second argument smuggled in behind a space.
valid_ip() {
    [[ "$1" =~ ^${SUBNET_PREFIX//./\\.}[0-9]{1,3}$ ]] || return 1
    local last="${1##*.}"
    (( last >= 2 && last <= 254 ))
}

# Names end up as a comment in wg0.conf. Alphanumerics, dash and underscore.
valid_name() {
    [[ "$1" =~ ^[A-Za-z0-9_-]{1,40}$ ]]
}

# Reading JSON with python3 rather than jq: it is present on any Ubuntu server
# without installing anything, which is one fewer thing to have forgotten when
# a router will not onboard at eleven at night. A missing or non-string field
# comes back empty and fails validation below.
read_fields() {
    "$PYTHON" -c '
import json, sys
# Pinned to \n. A platform that writes \r\n leaves a carriage return on the
# end of every field, each then fails its pattern, and the rejection blames
# the operator for a value that was perfectly correct.
sys.stdout.reconfigure(newline="\n")
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(2)          # unreadable, which is not the same as invalid
for key in ("name", "public_key", "tunnel_ip"):
    value = doc.get(key, "")
    print(value if isinstance(value, str) else "")
' "$1"
}

json_string() {
    "$PYTHON" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

# Checked once, loudly, rather than discovered per-request. Without this a
# missing interpreter makes every field come back empty, every request is
# rejected as "bad name", and the error sends you looking at what the operator
# typed instead of at this machine.
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    log "FATAL: $PYTHON not found. Set WG_PYTHON to your interpreter."
    exit 1
fi

if [ ! -x "$ADD_PEER" ]; then
    log "FATAL: $ADD_PEER is missing or not executable. Set WG_ADD_PEER."
    exit 1
fi

shopt -s nullglob
for request in "$SPOOL"/*.json; do
    id="$(basename "$request" .json)"
    result="$SPOOL/$id.result"

    reject() {
        log "REJECTED $id: $1"
        printf '{"ok":false,"error":%s}\n' "$(json_string "$1")" > "$result"
        rm -f "$request"
    }

    # Command substitution, not `mapfile < <(...)`: mapfile reports its own
    # success, not the subshell's, so a failing reader there is invisible and
    # every request comes back with three empty fields and a misleading
    # complaint about the name.
    #
    # A value containing a newline shifts the remaining fields, and every one
    # of them then fails its pattern — which is the outcome we want anyway.
    if ! raw="$(read_fields "$request")"; then
        reject "request was not readable JSON"; continue
    fi
    mapfile -t fields <<< "$raw"
    name="${fields[0]:-}"
    key="${fields[1]:-}"
    ip="${fields[2]:-}"

    if ! valid_name "$name"; then reject "bad name";       continue; fi
    if ! valid_key  "$key";  then reject "bad public key"; continue; fi
    if ! valid_ip   "$ip";   then reject "bad tunnel ip";  continue; fi

    # wg-add-peer.sh is idempotent: it checks whether the key is already in
    # wg0.conf, and applies with `wg set` rather than restarting the interface —
    # a restart would drop every other operator until their keepalive fires.
    if output="$("$ADD_PEER" "$name" "$key" "$ip" 2>&1)"; then
        log "applied $id: $name at $ip"
        printf '{"ok":true,"tunnel_ip":%s}\n' "$(json_string "$ip")" > "$result"
    else
        log "FAILED $id: $output"
        printf '{"ok":false,"error":%s}\n' "$(json_string "$output")" > "$result"
    fi

    rm -f "$request"
done

# Results are only advisory — whether a tunnel works is answered by Test
# connection dialling the router. Old ones are of no interest to anybody.
find "$SPOOL" -name '*.result' -mtime +7 -delete 2>/dev/null || true
