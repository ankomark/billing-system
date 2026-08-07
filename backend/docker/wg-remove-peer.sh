#!/bin/bash
#
# Take an operator's router off the tunnel.
#   sudo /home/deploy/wg-remove-peer.sh <router-public-key>
#
# The counterpart to wg-add-peer.sh, which existed on its own for long enough
# to prove why it should not have. Provisioning appended a [Peer] to wg0.conf
# and nothing ever took one back out, so deleting a router in the dashboard
# left its key on the server — able to bring up the tunnel and reach
# 10.10.0.1 — indefinitely. Found two of them on the first look: one for a
# router being decommissioned, and one belonging to an operator that no longer
# existed in the database at all, still handshaking twenty-one hours later.
#
# Removes it from the running interface and from the file. Doing only the
# first leaves it to come back at the next reboot; doing only the second
# leaves it working until then.
#
# Idempotent: a key that is not there is a success, because the outcome asked
# for is "this key cannot connect", and that is already true.

set -euo pipefail

KEY="${1:?usage: wg-remove-peer.sh <router-public-key>}"
CONF="${WG_CONF:-/etc/wireguard/wg0.conf}"
IFACE="${WG_IFACE:-wg0}"
PYTHON="${WG_PYTHON:-python3}"

if ! [[ "$KEY" =~ ^[A-Za-z0-9+/]{43}=$ ]]; then
    echo "not a WireGuard public key: $KEY" >&2
    exit 1
fi

# Live first. If the file edit fails afterwards the key is at least already
# refused, which is the half that matters this minute.
if wg show "$IFACE" public-key >/dev/null 2>&1; then
    wg set "$IFACE" peer "$KEY" remove 2>/dev/null || true
    echo "removed from the running interface"
else
    echo "warning: $IFACE is not up; editing the file only" >&2
fi

cp -a "$CONF" "$CONF.bak"

# Parsed rather than regexed. The blocks are written by wg-add-peer.sh today,
# but a config that has been edited by hand — which is how most of them end up
# — has whitespace and comments this would otherwise have to guess at.
"$PYTHON" - "$CONF" "$KEY" <<'PY'
import sys

path, key = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()

blocks, current = [], []
for line in lines:
    if line.strip().lower() == "[peer]":
        blocks.append(current)
        current = [line]
    elif line.strip().lower() == "[interface]":
        blocks.append(current)
        current = [line]
    else:
        current.append(line)
blocks.append(current)

def is_target(block):
    if not block or block[0].strip().lower() != "[peer]":
        return False
    for line in block:
        name, _, value = line.partition("=")
        if name.strip().lower() == "publickey" and value.strip() == key:
            return True
    return False

kept, dropped = [], 0
for block in blocks:
    if is_target(block):
        dropped += 1
        # A comment line immediately before the block names the operator, and
        # wg-add-peer.sh writes one. Leaving it behind labels the next peer
        # with the wrong operator's name.
        while kept and kept[-1].lstrip().startswith("#"):
            kept.pop()
        while kept and not kept[-1].strip():
            kept.pop()
        continue
    kept.extend(block)

open(path, "w").write("\n".join(kept).rstrip("\n") + "\n")
print(f"removed {dropped} peer block(s) from {path}")
PY

echo "=== peers now ==="
wg show "$IFACE" 2>/dev/null || grep -c "^\[Peer\]" "$CONF"
