#!/bin/bash
# WireGuard hub for operators whose routers cannot be dialled directly.
#
# Nearly every Kenyan operator is behind CGNAT -- Safaricom puts customers
# behind two layers of RFC1918 before the public internet, and Starlink
# residential does the same. The server cannot open a connection to any of
# them, so the routers open one to the server instead and hold it there.
#
# The tunnel carries management traffic only: the RouterOS API calls this
# platform makes. Subscriber browsing never enters it, which is why a 600MHz
# MIPS router is fine as a peer.
set -euo pipefail

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard >/dev/null

mkdir -p /etc/wireguard
cd /etc/wireguard
umask 077

if [ ! -f server-private.key ]; then
    wg genkey | tee server-private.key | wg pubkey > server-public.key
    echo "generated server keypair"
else
    echo "server keypair already exists, keeping it"
fi

if [ ! -f wg0.conf ]; then
cat > wg0.conf <<CONF
[Interface]
# The hub. Each operator's router gets 10.10.0.2, .3, .4 ...
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = $(cat server-private.key)

# Peers are appended below, one per operator router.
CONF
    chmod 600 wg0.conf
    echo "wrote wg0.conf"
else
    echo "wg0.conf already exists, keeping it"
fi

systemctl enable --now wg-quick@wg0 >/dev/null 2>&1 || systemctl restart wg-quick@wg0

# UDP, and only this port. WireGuard does not answer unauthenticated packets
# at all, so an unsolicited scan of 51820 gets silence rather than a handshake.
ufw allow 51820/udp >/dev/null

echo
echo "=== server public key (goes on the router) ==="
cat server-public.key
echo "=== interface ==="
wg show
echo "=== ufw ==="
ufw status | grep 51820
