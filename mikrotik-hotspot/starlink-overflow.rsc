# =====================================================================
# Starlink as overflow for the evening peak — MikroTik RouterOS v7
# =====================================================================
#
# Written for skylink3 (RB5009, ROS 7.19.4): Airtel on AIRTEL_WAN, Starlink in
# bypass mode on ether1. ether1 is already in the WAN interface list, so the
# default firewall covers it, and the hotspot masquerade rules match on source
# subnet rather than out-interface, so they apply to Starlink unchanged.
#
# ether1 needs nothing doing to it beyond this file. Checked on 2026-09-24:
# link down, not a bridge port, already in the WAN list, no address, no DHCP
# client. Airtel does not come in on it -- AIRTEL_WAN is a VLAN riding
# sfp-sfpplus1 -- so plugging the dish into ether1 cannot disturb the fibre.
# ether2, ether3, ether4 and ether6 are bridge ports feeding the access points
# and must not be used for this: ether2 alone was pushing 306 Mbit/s to
# customers when that was checked.
#
# WHAT IT DOES
#
# Every 30 seconds it counts logged-in hotspot clients.
#
#   * Above HIGH (390), the newest logins beyond 390 are put on Starlink,
#     until MAX (35) are on it. The 36th stays on Airtel.
#   * At or below LOW (380), everybody goes back to Airtel.
#   * In between, nothing changes. The gap stops a count wobbling around 390
#     from flipping people back and forth every half minute.
#
# WHY MAX EXISTS, AND WHY IT IS 35
#
# The dish carries 35 clients at a speed worth having. Beyond that it is
# sharing 25 Mbit/s too thin, and the 36th client is better off left on
# Airtel -- so that is where it is left. Nothing is "switched back": a client
# past the ceiling is never added to the list in the first place.
#
# The arithmetic behind 35, measured on skylink3 on 2026-09-23/24: Airtel
# delivers 262 Mbit/s at 297 clients, and the busiest hour of the week ran
# 278 Mbit/s across 482 customers -- 0.577 Mbit/s each. 25 Mbit/s split 35
# ways is 0.71 Mbit/s each, so the overflow group lands slightly ahead of what
# Airtel gives at peak, before counting the latency they gain.
#
# Without MAX, a 482-client peak moves 102 people onto 25 Mbit/s: 0.25 Mbit/s
# each. The overflow would punish the people it moves, and they are the newest
# logins -- customers who have just paid.
#
# Set MAX to 0 for no ceiling: move everyone above HIGH, however many that is.
# Raise it to 70 when a second 25 Mbit/s dish is in.
#
# WHY NOBODY IS CUT OFF WHEN THEY MOVE
#
# A connection leaves by the WAN it was opened on, and stays there until it
# closes. Moving a client only changes where their NEW connections go. Moving
# an open one would change its public address mid-stream -- a video call drops,
# a download restarts, a bank session logs out. So at 380 a client's open
# connections finish on Starlink and everything they open after that uses
# Airtel.
#
# If Starlink is unplugged or down, nobody is moved onto it, and anybody
# already moved falls back to Airtel through the via-starlink table's second
# route. Safe to install before the dish arrives: with ether1 dark it does
# nothing.
#
# FAILOVER
#
# Separately, Starlink's DHCP default route goes into main at distance 2, so if
# Airtel's route goes down EVERYONE -- and the billing tunnel -- moves to
# Starlink. Note that the Airtel route's check-gateway only pings Airtel's own
# gateway. On 2026-09-20 that gateway kept answering while traffic past it was
# being lost, so this alone would not have failed over that night.
#
# Measured again on 2026-09-23: 10 pings to the Airtel gateway came back with
# 10% loss and a 6ms floor, while 20 pings past it to 8.8.8.8 lost 5% at 145ms
# average. The gateway looks healthier than the path behind it, which is
# exactly the reading that keeps a dead route alive. A netwatch on something
# beyond the gateway is what would actually trigger failover -- not included
# here, because it belongs with the route rather than with overflow, and
# because raising the Airtel route's distance affects every customer at once.
#
# REMOVING IT
#
# Everything here carries a comment starting "STARLINK |". The removal block
# is at the bottom.
#
# =====================================================================


# ---------------------------------------------------------------------
# 1. Starlink on ether1
# ---------------------------------------------------------------------
# Its default route goes into main as the failover (distance 2, behind
# Airtel's 1). The overflow route in section 2 is kept pointed at the same
# gateway by the script in section 4.

# skylink3 already has a DHCP client on ether1 (defconf) putting Starlink into
# main at distance 1 -- equal to Airtel, so connections were split between the
# two by count and the 25 Mbit/s dish sat full while Airtel had room. This
# takes that client over rather than adding a second one on the same port.
/ip dhcp-client
set [find where interface=ether1] add-default-route=yes default-route-distance=2 \
    use-peer-dns=no use-peer-ntp=no comment="STARLINK | overflow WAN"


# ---------------------------------------------------------------------
# 2. A routing table for overflow clients
# ---------------------------------------------------------------------
# 100.64.0.1 is Starlink's usual gateway in bypass mode. The script corrects
# it to whatever DHCP actually hands out.
#
# The second route is the way home: if Starlink stops answering, overflow
# clients go back out through Airtel instead of nowhere.

/routing table
add name=via-starlink fib

/ip route
add dst-address=0.0.0.0/0 gateway=100.64.0.1%ether1 routing-table=via-starlink \
    distance=1 check-gateway=ping comment="STARLINK | via-starlink default"
add dst-address=0.0.0.0/0 gateway=102.0.38.141%AIRTEL_WAN \
    routing-table=via-starlink distance=2 \
    comment="STARLINK | via-starlink fallback to Airtel"


# ---------------------------------------------------------------------
# 3. Marking overflow traffic
# ---------------------------------------------------------------------
# via-starlink holds only a default route, so a destination on this router's
# own networks would find nothing there. These never leave by a WAN.

/ip firewall address-list
add list=starlink-local address=192.168.88.0/24 comment="STARLINK | hotspot"
add list=starlink-local address=172.20.0.0/22 comment="STARLINK | hotspot overflow pool"
add list=starlink-local address=192.168.90.0/24 comment="STARLINK | free internet"
add list=starlink-local address=10.10.0.0/24 comment="STARLINK | billing tunnel"

# connection-state=new: only connections opened while the client is on the
# list. See WHY NOBODY IS CUT OFF above.
/ip firewall mangle
add chain=prerouting action=mark-connection new-connection-mark=via-starlink \
    passthrough=yes connection-state=new connection-mark=no-mark \
    in-interface=bridge src-address-list=starlink-overflow \
    dst-address-list=!starlink-local dst-address-type=!local \
    comment="STARLINK | new connections from overflow clients"
add chain=prerouting action=mark-routing new-routing-mark=via-starlink \
    passthrough=no connection-mark=via-starlink in-interface=bridge \
    dst-address-list=!starlink-local \
    comment="STARLINK | send marked connections out Starlink"


# ---------------------------------------------------------------------
# 4. Deciding who is on Starlink
# ---------------------------------------------------------------------
# HIGH, LOW and MAX are the numbers to tune. HIGH is where overflow starts;
# MAX is how many the dish can actually carry, and is the one that protects
# the people being moved. See WHY MAX EXISTS above.
#
# Airtel's packet loss does not wait for the peak: 5% to 8.8.8.8 was measured
# at 179 clients on 2026-09-23, on a link then carrying 100 of the 262 Mbit/s
# it does at load. That loss is Airtel's transit, not congestion here, so
# lowering HIGH will not cure it -- it only decides how many customers get an
# alternative path.
#
# "Newest" is the order /ip hotspot active lists sessions in, which is login
# order. Entries carry a one-day timeout so a list left behind by a stopped
# scheduler empties itself.

/system script
add name=starlink-overflow policy=read,write \
    comment="STARLINK | moves clients above HIGH onto Starlink" source={
:local high 390
:local low 380
:local max 35
# Not "list": inside a `where`, "list=$list" compares the property to itself,
# matches every address-list entry on the router, and the script empties them
# all. That is what the first install on 2026-09-27 did.
:local ovList "starlink-overflow"

# Keep the overflow route on the gateway Starlink actually gave us.
:local dc [/ip dhcp-client find where comment="STARLINK | overflow WAN"]
:if ([:len $dc] > 0) do={
    :local gw [:tostr [/ip dhcp-client get [:pick $dc 0] gateway]]
    :if ([:len $gw] > 0) do={
        :local want ($gw . "%ether1")
        :foreach r in=[/ip route find where comment="STARLINK | via-starlink default"] do={
            :if ([:tostr [/ip route get $r gateway]] != $want) do={
                /ip route set $r gateway=$want
            }
        }
    }
}

:local n [/ip hotspot active print count-only]

# Forget overflow clients who have logged out.
:foreach e in=[/ip firewall address-list find where list=$ovList] do={
    :local a [/ip firewall address-list get $e address]
    :if ([:len [/ip hotspot active find where address=$a]] = 0) do={
        /ip firewall address-list remove $e
    }
}
:local held [:len [/ip firewall address-list find where list=$ovList]]

# Hold the ceiling from above as well as below. Only reachable when MAX has
# been lowered with people already on the dish -- otherwise nothing ever adds
# past it. Sending one back to Airtel costs them nothing in flight: their open
# connections finish where they started, and only new ones change WAN.
:if ($max > 0 && $held > $max) do={
    :local over ($held - $max)
    :foreach e in=[/ip firewall address-list find where list=$ovList] do={
        :if ($over > 0) do={
            /ip firewall address-list remove $e
            :set over ($over - 1)
            :set held ($held - 1)
        }
    }
    :log info "starlink-overflow: over the $max ceiling -- trimmed to $held"
}

:local starlinkUp false
:foreach r in=[/ip route find where comment="STARLINK | via-starlink default"] do={
    :if ([/ip route get $r active]) do={ :set starlinkUp true }
}

:if ($n <= $low) do={
    :if ($held > 0) do={
        /ip firewall address-list remove [find where list=$ovList]
        :log info "starlink-overflow: $n clients, at or below $low -- $held back to Airtel"
    }
} else={
    :if ($n > $high && $starlinkUp) do={
        :local need ($n - $high)
        # Never more than the dish can carry. max=0 means no ceiling, which is
        # "move everyone above high" -- see WHY MAX EXISTS at the top.
        :if ($max > 0 && $need > $max) do={ :set need $max }
        :if ($held < $need) do={
            :local ids [/ip hotspot active find]
            :local i ([:len $ids] - 1)
            :local added 0
            :while ($i >= 0 && ($held + $added) < $need) do={
                :local a [/ip hotspot active get [:pick $ids $i] address]
                :if ([:len [/ip firewall address-list find where list=$ovList address=$a]] = 0) do={
                    /ip firewall address-list add list=$ovList address=$a \
                        timeout=1d comment="STARLINK | overflow client"
                    :set added ($added + 1)
                }
                :set i ($i - 1)
            }
            :local total ($held + $added)
            :log info "starlink-overflow: $n clients -- $added more to Starlink, $total held of $need wanted"
        }
    }
}
}

/system scheduler
add name=starlink-overflow interval=30s start-time=startup policy=read,write \
    on-event="/system script run starlink-overflow" \
    comment="STARLINK | runs the overflow check"


# =====================================================================
# THE DAY THE DISH ARRIVES
# =====================================================================
#
# Paste this file first, with ether1 still dark. Nothing moves: the overflow
# route is inactive, so $starlinkUp stays false and no client is ever added to
# the list. That is the point of installing it early -- the parts that could
# be wrong are wrong while they cost nothing.
#
# Then plug the dish into ether1, in bypass mode, and check in this order:
#
#   /ip dhcp-client print where comment~"STARLINK"
#       expect bound, with an address in 100.64.x and a gateway
#
#   /ip route print where comment~"STARLINK"
#       expect the via-starlink default ACTIVE on the gateway just handed out;
#       the script rewrites it within 30 seconds if it differs
#
#   /ping 8.8.8.8 interface=ether1 count=10
#       the dish itself, before any customer is on it
#
#   /log print where message~"starlink-overflow"
#       one line each time it moves or returns anybody
#
#   /ip firewall address-list print where list=starlink-overflow
#       who is on the dish right now
#
# Watch the first evening at HIGH: with 390 and MAX 35, nothing at all happens
# until the 391st client logs in, and at most 35 are ever on the dish. If the
# log shows "35 held of 102 wanted" every 30 seconds, that is MAX doing its
# job, not a fault -- the other 67 are on Airtel, which is where you want them.
#
# 35 is a guess until the dish is in. Check it on the first evening:
#
#   /interface monitor-traffic interface=ether1 once
#
# with a full list. If that reads well under 25 Mbit/s, the customers on it
# are not using their share and MAX can rise. If it is pinned at 25, MAX is
# already too high and the people on it are queuing.
#
# =====================================================================
# REMOVAL -- paste this block to take all of the above back off
# =====================================================================
#
# /system scheduler remove [find where comment~"^STARLINK"]
# /system script remove [find where comment~"^STARLINK"]
# /ip firewall mangle remove [find where comment~"^STARLINK"]
# /ip firewall address-list remove [find where list=starlink-overflow]
# /ip firewall address-list remove [find where list=starlink-local]
# /ip route remove [find where comment~"^STARLINK"]
# /routing table remove [find where name=via-starlink]
# /ip dhcp-client set [find where comment~"^STARLINK"] default-route-distance=1 \
#     use-peer-dns=yes use-peer-ntp=yes comment=defconf
