#!/bin/bash
# Add an operator's router to the tunnel.
#   sudo /home/deploy/wg-add-peer.sh <operator-name> <router-public-key> <tunnel-ip>
# e.g. sudo /home/deploy/wg-add-peer.sh fidel xHSiEC+B4H...= 10.10.0.2
set -euo pipefail

NAME="${1:?operator name}"
PUBKEY="${2:?router public key}"
IP="${3:?tunnel ip, e.g. 10.10.0.2}"

CONF=/etc/wireguard/wg0.conf

if grep -q "$PUBKEY" "$CONF" 2>/dev/null; then
    echo "peer already present, not adding twice"
else
    cat >> "$CONF" <<CONF

# $NAME
[Peer]
PublicKey = $PUBKEY
AllowedIPs = $IP/32
CONF
    echo "appended $NAME to $CONF"
fi

# Applied live rather than by restarting the interface: a restart drops every
# other operator's tunnel, and they are behind CGNAT -- they reconnect only
# when their own keepalive next fires, so a restart to onboard one operator
# takes the rest offline for as long as that takes.
wg set wg0 peer "$PUBKEY" allowed-ips "$IP/32"

echo "=== peers now ==="
wg show wg0
