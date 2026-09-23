# =====================================================================
# Closing what an HTTP injector rides - MikroTik RouterOS v7
# =====================================================================
#
# Audited on skylink3 (RB5009, ROS 7.19.4) on 2026-09-23. Two ways past the
# portal were open; everything else an injector app looks for was already
# shut.
#
# WHAT AN INJECTOR ACTUALLY DOES
#
# It opens a TCP connection to its own tunnel server and writes a request
# whose Host header -- or, over TLS, whose SNI -- names a host the hotspot
# permits before login. The hotspot reads the name, matches its walled garden,
# and forwards. Nothing is spoofed at the IP layer and nothing is broken into:
# the portal is simply told a different name than the one it is talking to.
#
# So the walled garden is the whole game. A host rule trusts a string the
# client controls. An IP rule trusts the destination address, which the client
# cannot lie about and still receive a reply.
#
# 1. THE WALLED GARDEN
#
# skylink3 carried both, for the same destination:
#
#   /ip hotspot walled-garden      dst-host=api.smartbillsolution.com
#   /ip hotspot walled-garden ip   dst-address=167.233.247.151  accept
#
# api.smartbillsolution.com resolves to 167.233.247.151, so the IP rule
# already carries every byte the portal needs. The host rule adds nothing
# except a name an injector can borrow.
#
# Disabled rather than removed: one flag puts it back, and the row is the only
# record that it was ever there.
#
# NOTE: `/ip hotspot walled-garden` in ROS 7 has no `dst-address` parameter --
# the two cannot be combined into one rule that requires both. The API answers
# "unknown parameter dst-address". Disabling the host rule is the fix.

/ip hotspot walled-garden
set [find where dst-host="api.smartbillsolution.com"] disabled=yes \
    comment="Billing API - disabled, superseded by the IP rule; dst-host is spoofable"

# The rule that must exist first. If this is missing, do NOT disable the one
# above -- that takes the portal off the air for everyone.
/ip hotspot walled-garden ip
add dst-address=167.233.247.151 action=accept comment="Billing API direct"


# 2. THE ROUTER'S OWN SERVICES
#
# The input chain ends with `drop in-interface-list=!LAN`, and the hotspot
# bridge is in LAN. So every customer device -- logged in or not -- can reach
# whatever /ip service is listening on. On skylink3 that was ftp, telnet, ssh,
# winbox, www and api-ssl, all on address=ANY. Only `api` was restricted.
#
# This is not an injector's path onto the network; it is how somebody who
# knows RouterOS gets the router itself. Telnet and FTP carry the password in
# clear text across a network any customer is already on.
#
# steps.md:1821 has said all along that these belong on 10.10.0.1/32. This is
# that, written down where it can be pasted.

/ip service
disable ftp,telnet,api-ssl
set ssh address=10.10.0.1/32
set www address=10.10.0.1/32
set winbox address=10.10.0.1/32

# WinBox by MAC still works after this and ignores IP rules entirely, which is
# the way back in if the tunnel is down. See steps.md:156.
#
# The captive portal is NOT served by `www`. The hotspot servlet listens on
# 64873-64875 and the hotspot NAT chain redirects to it, so restricting the
# router's own web admin does not touch the login page.


# 3. WHAT IS LEFT OPEN, DELIBERATELY
#
# DNS. Every port 53 query from an unauthenticated client is redirected to the
# router's resolver, which recurses to 8.8.8.8 and 1.1.1.1. That is what lets
# a phone resolve the portal before it has logged in, and it is also what
# iodine and dnstt tunnel over. There is no way to have the first without the
# second. It runs at tens of kbps, so it is a nuisance rather than a business
# risk -- watch for a client making an abnormal number of queries rather than
# trying to block it.
#
# MAC cloning. `login-by=mac` with `mac-auth-mode=mac-as-username` means
# whoever holds a paying customer's address holds their session. shared-users=1
# means the two collide rather than both being served, and
# clear-duplicate-sessions runs every five minutes.


# 4. VERIFY
#
# /ip hotspot walled-garden print
# /ip hotspot walled-garden ip print
# /ip service print
# /ip proxy print          -- expect enabled=no
# /ip socks print          -- expect enabled=no
# /ip hotspot ip-binding print where type=bypassed
#
# Then, from the platform's side of the tunnel:
#
# ssh deploy@SERVER_IP 'timeout 5 bash -c "</dev/tcp/10.10.0.N/8728"'


# =====================================================================
# ROLLBACK -- paste this to undo everything above
# =====================================================================
#
# /ip hotspot walled-garden set [find where dst-host="api.smartbillsolution.com"] disabled=no
# /ip service enable ftp,telnet,api-ssl
# /ip service set ssh address=""
# /ip service set www address=""
# /ip service set winbox address=""
