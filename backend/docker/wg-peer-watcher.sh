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

log() { logger -t wg-peer-watcher "$*"; echo "$*"; }

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

shopt -s nullglob
for request in "$SPOOL"/*.json; do
    id="$(basename "$request" .json)"
    result="$SPOOL/$id.result"

    # jq -r on a missing key yields "null", which fails every check below.
    name="$(jq -r '.name // ""'       "$request" 2>/dev/null || echo "")"
    key="$( jq -r '.public_key // ""' "$request" 2>/dev/null || echo "")"
    ip="$(  jq -r '.tunnel_ip // ""'  "$request" 2>/dev/null || echo "")"

    reject() {
        log "REJECTED $id: $1"
        printf '{"ok":false,"error":%s}\n' "$(jq -Rn --arg e "$1" '$e')" > "$result"
        rm -f "$request"
    }

    if ! valid_name "$name"; then reject "bad name";       continue; fi
    if ! valid_key  "$key";  then reject "bad public key"; continue; fi
    if ! valid_ip   "$ip";   then reject "bad tunnel ip";  continue; fi

    # wg-add-peer.sh is idempotent: it checks whether the key is already in
    # wg0.conf, and applies with `wg set` rather than restarting the interface —
    # a restart would drop every other operator until their keepalive fires.
    if output="$("$ADD_PEER" "$name" "$key" "$ip" 2>&1)"; then
        log "applied $id: $name at $ip"
        printf '{"ok":true,"tunnel_ip":%s}\n' "$(jq -Rn --arg i "$ip" '$i')" > "$result"
    else
        log "FAILED $id: $output"
        printf '{"ok":false,"error":%s}\n' "$(jq -Rn --arg e "$output" '$e')" > "$result"
    fi

    rm -f "$request"
done

# Results are only advisory — whether a tunnel works is answered by Test
# connection dialling the router. Old ones are of no interest to anybody.
find "$SPOOL" -name '*.result' -mtime +7 -delete 2>/dev/null || true
