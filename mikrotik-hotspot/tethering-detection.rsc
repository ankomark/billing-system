# =====================================================================
# Hotspot sharing detection — MikroTik RouterOS (v6 and v7)
# =====================================================================
#
# These are the same rules the billing system installs through the API when an
# operator sets TETHERING_POLICY. They are here for anyone who would rather
# paste them in and watch what they catch before letting anything act on them.
#
# WHAT IT DOES
#
# Every IP packet carries a hop counter (TTL) that its sender sets to a round
# number — 64 on Android, iOS, Linux and macOS, 128 on Windows, 255 on some
# appliances — and every router it crosses subtracts one. A phone connected to
# your hotspot reaches you with the number it started with. A laptop behind
# that phone's own hotspot has crossed the phone, so it arrives one lower.
#
# These rules write the offending source addresses into address lists. On their
# own they change nothing for anybody: no drop, no redirect, no queue. Watch
# the lists for a few days on your own network before deciding what to do
# about what turns up in them.
#
# WHAT IT IS NOT
#
# It says "this packet crossed a router", not "this person is cheating". A
# subscriber who plugs your WiFi into their own travel router looks identical,
# and so does anyone running a virtual machine with a NATted adapter. It is
# also defeatable — pinning the outgoing TTL back to 64 is one line on a rooted
# Android — so anyone who has bothered will never appear here.
#
# Treat what lands in these lists as a reason to look, not a verdict.
#
# TWO ROUTEROS FACTS THAT COST PEOPLE HOURS
#
#   * The ttl matcher exists ONLY in /ip firewall mangle. The same rule under
#     /ip firewall filter is rejected. That is why every recipe you will find
#     marks in mangle and matches the mark in filter.
#   * In chain=forward the value has already been decremented by THIS router,
#     so you would be looking for 62 rather than 63. chain=prerouting is the
#     value as it arrived, which is what all the numbers above refer to.
#
# AND ONE THAT COSTS MORE
#
#   hotspot=auth is not decoration. Without it the rule matches everything
#   crossing prerouting, including replies coming back from the internet, which
#   arrive with whatever is left after a dozen hops — and within a minute your
#   address list is full of the addresses of web servers.
#
# =====================================================================


# ---------------------------------------------------------------------
# 1. Normal — the device is talking to this router directly
# ---------------------------------------------------------------------
# Recorded, not punished. An address that appears in BOTH this list and
# tether-hop1 in the same window is the strongest signal there is: the phone
# itself browsing while something else browses through it.

/ip firewall mangle
add chain=prerouting action=add-src-to-address-list address-list=tether-normal \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:64 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=64"
add chain=prerouting action=add-src-to-address-list address-list=tether-normal \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:128 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=128"
add chain=prerouting action=add-src-to-address-list address-list=tether-normal \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:255 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=255"

# ---------------------------------------------------------------------
# 2. One hop — a device behind the subscriber's phone
# ---------------------------------------------------------------------

add chain=prerouting action=add-src-to-address-list address-list=tether-hop1 \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:63 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=63"
add chain=prerouting action=add-src-to-address-list address-list=tether-hop1 \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:127 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=127"
add chain=prerouting action=add-src-to-address-list address-list=tether-hop1 \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:254 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=254"

# ---------------------------------------------------------------------
# 3. Two hops — a router behind the phone
# ---------------------------------------------------------------------

add chain=prerouting action=add-src-to-address-list address-list=tether-hop2 \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:62 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=62"
add chain=prerouting action=add-src-to-address-list address-list=tether-hop2 \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:126 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=126"
add chain=prerouting action=add-src-to-address-list address-list=tether-hop2 \
    address-list-timeout=10m hotspot=auth connection-state=new ttl=equal:253 \
    comment="AUTO | WIFI BILLING SYSTEM | tether ttl=253"

# Note what is deliberately absent: a rule for "anything below 64". A VPN, a
# distant proxy or a mangled packet can leave any value at all, and a rule that
# treats every one of them as sharing will accuse people who are doing nothing
# wrong. Two hops is as far as this goes.

# ---------------------------------------------------------------------
# 4. Busy — more connections than one handset holds
# ---------------------------------------------------------------------
# The one rule above does not depend on the hop counter, and so the one that
# survives somebody pinning it. A phone browsing sits in the low tens of open
# connections; four devices streaming does not.
#
# Corroboration only. One enthusiastic torrent client passes this on its own,
# so an address in this list and nowhere else means nothing. The /32 is what
# makes the limit per address rather than per subnet — without it, one busy
# subscriber puts the whole hotspot range on the list.

add chain=prerouting action=add-src-to-address-list address-list=tether-busy \
    address-list-timeout=10m hotspot=auth connection-state=new \
    connection-limit=100,32 \
    comment="AUTO | WIFI BILLING SYSTEM | tether connections>100"

# connection-state=new means only the first packet of each connection is
# examined. On a busy hotspot that is the difference between a rule set that
# costs nothing and one you notice in the CPU graph.


# =====================================================================
# THE HOLE THIS CANNOT SEE INTO — CHECK THIS FIRST
# =====================================================================
#
#   /ipv6 address print
#
# MikroTik's hotspot is IPv4 only. It does not intercept IPv6, does not
# authenticate it, and none of the rules above match it — the hop counter lives
# in a different header, matched by a different firewall (/ipv6 firewall mangle,
# hop-limit=equal:63).
#
# So if that command shows a global address (anything not starting fe80) on a
# client-facing interface, a tethered device that prefers IPv6 never appears in
# any of these lists. Worse, a device that never logged in at all may not need
# to. That is a hole in the hotspot, not just in this detector.
#
# If you are not deliberately running IPv6, turn it off on the client bridge
# and the question goes away.
#
#
# =====================================================================
# WATCHING IT
# =====================================================================
#
#   /ip firewall address-list print where list=tether-hop1
#   /ip firewall mangle print stats where comment~"tether"
#
# The second one is the one to look at first. A rule with zero packets after an
# hour is a rule that is not matching, and the usual cause is that the hotspot
# server is on a different interface than you think.
#
# To see who an address belongs to:
#
#   /ip hotspot active print where address=10.5.50.14
#
#
# =====================================================================
# ACTING ON IT — read this part twice
# =====================================================================
#
# The billing system does this itself, and does it only after the same address
# has turned up in several sweeps minutes apart, because one sighting is not
# evidence of anything. If you are acting by hand, apply the same patience.
#
# Slow them down rather than cutting them off. A throttled connection is
# unpleasant to share and still usable by the person who paid for it; a dropped
# one is indistinguishable from your network being broken, and that is a
# support call and a refund, not a deterrent.
#
#   /queue simple
#   add name="TETHER 10.5.50.14" target=10.5.50.14/32 max-limit=512k/512k \
#       comment="AUTO | WIFI BILLING SYSTEM | tether"
#
# If you want them back at the login page, end the session. A firewall rule
# cannot do this: once a client is authenticated the hotspot passes their
# traffic, so dropping packets gives them a broken connection rather than a
# login form. Removing the session returns them to unauthenticated, and the
# next page they open is intercepted like any new arrival's.
#
#   /ip hotspot active remove [find address=10.5.50.14]
#
# Their account is untouched — they can log straight back in. That is the
# point. It interrupts the sharing and gives you a reason to tell them why,
# without taking away what they paid for.
#
# Whatever you apply, write down how it comes off again. A throttle queue that
# nobody removes is a customer paying for 10 Mbps and getting 512 kbps for the
# rest of the month.
#
#
# =====================================================================
# TAKING IT ALL BACK OFF
# =====================================================================
#
#   /ip firewall mangle remove [find comment~"WIFI BILLING SYSTEM \\| tether"]
#   /ip firewall address-list remove [find list~"^tether-"]
#   /queue simple remove [find comment~"WIFI BILLING SYSTEM \\| tether"]
#
# Do the queues even if you think there are none. A throttle applied to an
# address and never lifted is the worst thing in this file: the subscriber it
# was meant for has long since moved to another lease, and whoever holds that
# address now is paying full price for 512 kbps with nothing on the router
# saying why.
#
# Or, from the billing server, for one operator or all of them:
#
#   python manage.py tethering_rules remove --tenant <slug>
