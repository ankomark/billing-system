# =====================================================================
# Peer-to-peer detection and blocking — MikroTik RouterOS 7
# =====================================================================
#
# Written after Starlink sent a copyright notice naming the whole site on
# 2026-09-01. Because every subscriber leaves through one Starlink address, a
# notice arrives with no way to tell which of them caused it — the operator is
# the only person it can be addressed to.
#
# WHAT THIS DOES, AND WHAT IT HONESTLY CANNOT
#
# It stops a torrent client that nobody has reconfigured, and it names the
# addresses behaving like one. It does not stop somebody who changes the port,
# and it cannot see inside a VPN at all. Anybody determined is still invisible.
#
# That is worth stating plainly, because the value here is not a perfect block.
# It is: default clients stop working, deliberate ones become visible, and the
# operator can answer the next notice with a name instead of a shrug. Those are
# the "reasonable measures" the AUP is really asking for.
#
# THE TWO HALVES ARE DELIBERATELY DIFFERENT
#
#   * The port rules DROP. Ports 6881-6999 are the BitTorrent range and
#     essentially nothing else uses them, so there is no legitimate traffic to
#     lose. Anything caught is recorded first, then dropped.
#
#   * The connection-count rule only WRITES A NAME DOWN. A torrent client opens
#     hundreds of simultaneous connections and browsing rarely passes a hundred,
#     which makes the count a good signal — and encryption does not hide it,
#     unlike anything layer7 could match. But it is still only a signal: a
#     household of six phones on one PPPoE line looks similar. Watch the list
#     for a few days before letting anything act on it, the same way
#     tethering-detection.rsc argues for its own lists.
#
# WHY THERE IS NO LAYER7 RULE HERE
#
# The BitTorrent handshake regex is the recipe everyone posts. It matches only
# the first few packets of a connection, costs real CPU on every one of them,
# and modern clients encrypt by default — which defeats it entirely. It buys
# very little for what it costs, so it is left out on purpose.
#
# ORDER MATTERS
#
# The add-to-list rules sit ABOVE the drops. A dropped packet stops being
# processed, so recording who tried has to happen first or the list stays empty
# and you learn nothing about who is trying.
#
# The rules append to the end of the forward chain, which is correct here: the
# chain's default policy is accept and the defconf rules above only accept
# established/related traffic. A new torrent connection is still new, so it
# reaches these.
#
# =====================================================================


# ---------------------------------------------------------------------
# 1. Who is trying — recorded before anything is dropped
# ---------------------------------------------------------------------
# A day's timeout, because a copyright notice arrives long after the fact and
# a list that has already forgotten is no use when it does.

/ip firewall filter
add chain=forward action=add-src-to-address-list protocol=tcp \
    dst-port=6881-6999 address-list=p2p-ports address-list-timeout=1d \
    comment="AUTO | WIFI BILLING SYSTEM | p2p tcp port seen"
add chain=forward action=add-src-to-address-list protocol=udp \
    dst-port=6881-6999 address-list=p2p-ports address-list-timeout=1d \
    comment="AUTO | WIFI BILLING SYSTEM | p2p udp port seen"


# ---------------------------------------------------------------------
# 2. The block itself
# ---------------------------------------------------------------------
# Deliberately not logged. A torrent client retries constantly, and log=yes
# here fills the router's buffer within minutes and pushes out everything you
# would actually want to read. The address list above is the record.

add chain=forward action=drop protocol=tcp dst-port=6881-6999 \
    comment="AUTO | WIFI BILLING SYSTEM | p2p tcp drop"
add chain=forward action=drop protocol=udp dst-port=6881-6999 \
    comment="AUTO | WIFI BILLING SYSTEM | p2p udp drop"


# ---------------------------------------------------------------------
# 3. Behaving like a torrent client, whatever port it uses
# ---------------------------------------------------------------------
# Records only. Nothing drops on the strength of this, and nothing should
# until the list has been watched on a real network for a few days.
#
# 150 concurrent TCP connections from one address is high for browsing and
# streaming, and low for a torrent client. Scoped to the hotspot pool: a PPPoE
# line is a whole household behind one address and would sit near the limit
# legitimately, which is exactly the false positive to avoid.

add chain=forward action=add-src-to-address-list protocol=tcp \
    connection-limit=150,32 src-address=192.168.88.0/24 \
    address-list=p2p-suspect address-list-timeout=2h \
    comment="AUTO | WIFI BILLING SYSTEM | many concurrent connections"


# ---------------------------------------------------------------------
# 4. When you have watched long enough
# ---------------------------------------------------------------------
# Do not paste this with the rest. It is here so the decision is written down
# next to the evidence it depends on.
#
# Throttling reads better than dropping: somebody who has paid should not be
# cut off the internet on a signal this soft, and a torrent at 128k stops being
# worth running while a web page still loads.
#
#   /queue simple
#   add name=p2p-throttle target=p2p-suspect max-limit=128k/128k \
#       comment="AUTO | WIFI BILLING SYSTEM | p2p throttle"
#
# Dropping outright, if you decide the list has earned it:
#
#   /ip firewall filter
#   add chain=forward action=drop src-address-list=p2p-suspect \
#       comment="AUTO | WIFI BILLING SYSTEM | p2p enforce"


# ---------------------------------------------------------------------
# 5. Reading the lists
# ---------------------------------------------------------------------
#   /ip firewall address-list print where list=p2p-ports
#   /ip firewall address-list print where list=p2p-suspect
#
# An address alone does not name a subscriber. Cross it with the lease and the
# hotspot host to get the MAC, which is what the billing system knows them by:
#
#   /ip dhcp-server lease print where address="192.168.88.x"
#   /ip hotspot host print where address="192.168.88.x"
#
# Note the address is only meaningful while the lease lasts. Answering a notice
# about last Tuesday needs a record kept over time, which the router does not
# do — that belongs on the server.


# ---------------------------------------------------------------------
# 6. Removing all of this
# ---------------------------------------------------------------------
#   /ip firewall filter remove [find comment~"WIFI BILLING SYSTEM \\| p2p"]
#   /ip firewall filter remove [find comment~"many concurrent connections"]
