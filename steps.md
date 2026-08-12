# From nothing to a customer online

The build order, as actually performed, with the things that went wrong kept in.
`GUIDE.md` explains *why* each piece exists; this is the sequence and the
commands.

Placeholders: `example.com` is your domain, `SERVER_IP` your Hetzner address,
`TENANT_TOKEN` an operator's token. Real values live in your password manager,
not here — this file is in a public repository.

**Parts 1–6 you do once.** **Parts 7–11 you repeat for every operator.**

---

## Cheat sheet — a MikroTik from the box, in order

Every command for one router, in the order you run them. Part 9 explains each
and what it looks like when it goes wrong; this is for the second router and
the twentieth, when you only need the sequence.

Substitute throughout: **`bridgeLocal`** (read the real name off
`/interface/bridge/print` — an RB951 does not call it `bridge`, an RB5009 does),
**`YOUR-SSID`**, **`api.example.com`** and **`SERVER_IP`**, and
**`AA:BB:CC:DD:EE:FF`** (your own laptop's MAC).

**Substitute them before you press enter.** RouterOS accepts
`AA:BB:CC:DD:EE:FF` as a perfectly valid MAC and binds it, so the placeholder
does not fail — it succeeds against a device that does not exist, and you find
out later when the bypass you thought you had is not there. The same goes for
anything in angle brackets.

### Which board you have

Two shapes turn up, and the difference decides whether **stage 5** is a
configuration job or a shopping list:

| | Board with a radio | Board without one |
|---|---|---|
| Example | RB951, hAP ac² | **RB5009UG+S+**, RB4011, CCR |
| `/interface/print` shows | `wlan1` | ether ports only |
| Bridge is called | `bridgeLocal` on an RB951 | `bridge` |
| The WiFi | configured in **stage 5A** | a separate AP — **stage 5B** |

Everything else is identical. The hotspot runs on the bridge either way, and a
board with no radio is not a worse hotspot gateway — an RB5009's four ARM64
cores carry far more sessions than an RB951's 600 MHz MIPS. It just cannot
broadcast, so somebody else's access point has to, and **how that AP is
configured decides whether your billing works at all**. See stage 5B.

```
/system/resource/print
/interface/print
```

`board-name` and the absence of any `wlan` interface answer it in one look. On
a radio-less board `/interface/wireless/print` returns *no such command* — the
package is absent because the hardware is. That is not a fault.

### 1. Look before you touch

| Command | Expect | If it is not that |
|---|---|---|
| `/system/resource/print` | `version: 7.13` or newer | On 6.x nothing below works — device-mode and WireGuard both need 7. Upgrade first |
| `/system/device-mode/print` | `hotspot: yes`, `proxy: yes` | Either `no` → **stage 4** |
| `/interface/bridge/print` | one bridge — **write the name down** | `bridgeLocal` on an RB951. Use whatever it says everywhere below |
| `/interface/print` | `wlan1` present, no `X` | No `wlan` at all → radio-less board → **stage 5B**. `X`, or `;;; managed by CAPsMAN` → **stage 5A** |
| `/interface/bridge/port/print` | ether2–5 and `wlan1`, **not `ether1`** | `ether1` listed → it is a switch, not a router → **stage 2** |
| `/ip/address/print` | one on the bridge, one on `ether1` | Same address on **two** interfaces → the old DHCP client is still running → `/ip/dhcp-client/remove [find interface=bridgeLocal]` |
| `/ip/dhcp-client/print` | on `ether1`, `status: bound` | On the *bridge* → **stage 2**. `searching` forever → power-cycle the modem; some ISPs bind to the first MAC they see |
| `/ip/route/print` | exactly one `0.0.0.0/0`, **no `+`** | `+` flags = two default routes, one dead → remove the stale DHCP client as above |
| `/ip/firewall/nat/print` | a `masquerade` with `out-interface-list=WAN` | Empty → nothing is being translated → the NAT line in **stage 2** |
| `/ip/firewall/filter/print` | seven rules | Empty → no input protection at all → **stage 3** |
| `/ip/firewall/filter/print where action=fasttrack-connection` | **nothing** | A `defconf: fasttrack` rule → remove it now, see below. MikroTik ships one and it must not run on a hotspot |
| `/ping 8.8.8.8 count=3` | `0% packet loss` | 100% → the router has no WAN; fix that before anything else. ~66% with an odd `host unreachable` from its own address → two default routes again |
| `/system/clock/print` | today's date and time | Wrong → **stage 4**. Session expiry is decided by this clock |

**Remove fasttrack before you go on**, on any board that arrived with MikroTik's
default configuration:

```
/ip/firewall/filter/remove [find action=fasttrack-connection]
/ip/firewall/filter/print where chain=forward
```

Fasttrack shunts established connections past the firewall **and past hotspot
accounting**, so the byte counters stop incrementing and
`collect_hotspot_usage` reads exactly those counters. Every subscriber then
appears to have used almost nothing. Nothing breaks visibly — customers get
online, the portal works, vouchers redeem — and you find out from a usage
report weeks later.

Stage 3 builds a rule set without one, so this only bites when defconf is
present, which is precisely the case where you skip stage 3 and assume the
default set is fine. **A factory reset puts it back**, so re-check after any
reset rather than trusting that you did this once.

The remaining dynamic rule `;;; special dummy rule to show fasttrack counters`
is a counter placeholder and disappears on reboot. Confirm the forward chain
still holds `accept established,related,untracked`, `drop invalid` and `drop
all from WAN not DSTNATed` afterwards.

### 2. Make it a router — only if `ether1` was in the bridge

Destructive line last; it drops your session. See the Debug entry for why the
order matters.

```
/ip/address/add address=192.168.88.1/24 interface=bridgeLocal
/ip/pool/add name=dhcp-pool ranges=192.168.88.10-192.168.88.254
/ip/dhcp-server/add name=dhcp-lan interface=bridgeLocal address-pool=dhcp-pool disabled=no
/ip/dhcp-server/network/add address=192.168.88.0/24 gateway=192.168.88.1 dns-server=8.8.8.8,1.1.1.1
/ip/dns/set allow-remote-requests=yes
/interface/list/add name=WAN
/interface/list/add name=LAN
/interface/list/member/add list=WAN interface=ether1
/interface/list/member/add list=LAN interface=bridgeLocal
/ip/firewall/nat/add chain=srcnat action=masquerade out-interface-list=WAN
/interface/bridge/port/remove [find interface=ether1]
/ip/dhcp-client/add interface=ether1 use-peer-dns=yes add-default-route=yes disabled=no
/ip/dhcp-client/remove [find interface=bridgeLocal]
```

Your session dies on that last line. Renew on the laptop
(`ipconfig /release && ipconfig /renew`) and reconnect to **`192.168.88.1`**,
not the MAC.

| Check | Expect | If it is not that |
|---|---|---|
| `/ip/route/print` | one `0.0.0.0/0` via ether1, no `+` | Two, with `+` → the last line did not run → run it now |
| `/ip/address/print` | `192.168.88.1/24` on the bridge, a dynamic one on `ether1` | Same address twice → same cause, same fix |
| `/ping 8.8.8.8 count=5` | `0% packet loss` | ~66% → still two routes. 100% → check `/ip/dhcp-client/print` is `bound` |
| laptop `ipconfig` | gateway `192.168.88.1` | Still `192.168.8.1` → it is answering the upstream, not this router → release/renew again |

If you lose the session before the last line, nothing is broken — the router
still holds its old address and you can reconnect there and carry on.

### 3. Base firewall — only if the filter table was empty

Check `/interface/list/member/print` shows the bridge in `LAN` first, or the
fourth line locks you out. **No fasttrack rule** — it bypasses hotspot
accounting and every customer then looks like they used nothing.

```
/ip/firewall/filter/add chain=input action=accept connection-state=established,related,untracked
/ip/firewall/filter/add chain=input action=drop connection-state=invalid
/ip/firewall/filter/add chain=input action=accept protocol=icmp
/ip/firewall/filter/add chain=input action=drop in-interface-list=!LAN
/ip/firewall/filter/add chain=forward action=accept connection-state=established,related,untracked
/ip/firewall/filter/add chain=forward action=drop connection-state=invalid
/ip/firewall/filter/add chain=forward action=drop connection-state=new connection-nat-state=!dstnat in-interface-list=WAN
```

| Check | Expect | If it is not that |
|---|---|---|
| `/ip/firewall/filter/print where !dynamic` | seven rules, accepts above drops | Out of order → `/ip/firewall/filter/move <from> <to>`; RouterOS appends, so a re-paste lands at the bottom |
| `ping 192.168.88.1` from the laptop | replies | Silence → the `!LAN` rule locked you out → reconnect **WinBox by MAC** (IP rules do not apply to it) and `/ip/firewall/filter/remove [find comment=""] ` or add the bridge to `LAN` |
| `ping 8.8.8.8` from the laptop | replies | Silence → the forward rules are wrong; `/ip/firewall/filter/print` and check the two `accept established` lines are above their drops |

### 4. Device mode and the clock

`hotspot` is already `yes` on a `home` board; `proxy` is not, and the hostname
walled-garden rules need it. **Power-cycle for ten seconds only if you changed
something** — a software reboot does not count.

```
/system/device-mode/update hotspot=yes proxy=yes
/system/ntp/client/set enabled=yes servers=time.cloudflare.com,pool.ntp.org
/system/clock/set time-zone-name=Africa/Nairobi
/system/ntp/client/print
```

| Check | Expect | If it is not that |
|---|---|---|
| `/system/device-mode/print` | `hotspot: yes`, `proxy: yes` | Still `no` → the five-minute window lapsed. Run the update again and pull the power faster. A software reboot never counts |
| `/system/ntp/client/print` | `status: synchronized` | `using-servers` or blank → the WAN is not up yet, or UDP 123 is blocked upstream. Re-check after a minute |
| `/system/clock/print` | matches your phone | Hours out → wrong `time-zone-name`; tab-complete it |

### 5A. The radio — boards that have one

Open, no WPA — customers authenticate at the portal. Free it from CAPsMAN
first or nothing sticks.

```
/interface/wireless/cap/print
/interface/wireless/cap/set enabled=no
/interface/wireless/security-profiles/set default mode=none
/interface/wireless/set wlan1 mode=ap-bridge ssid="YOUR-SSID" band=2ghz-b/g/n channel-width=20mhz frequency=2437 security-profile=default default-forwarding=no country=kenya disabled=no
/interface/bridge/port/add bridge=bridgeLocal interface=wlan1
/interface/wireless/monitor wlan1 once
```

| Check | Expect | If it is not that |
|---|---|---|
| `/interface/wireless/print` | no `;;; managed by CAPsMAN`, no `X` | Comment still there → `/interface/wireless/cap/set enabled=no` did not take; check `/caps-man/manager/print` too |
| `/interface/wireless/monitor wlan1 once` | `status: running-ap` | `disabled` → the `disabled=no` did not apply. Anything else → try `country=no_country_set` to test whether the regulatory profile is refusing your channel, then put the country back with `frequency=2412` |
| `/interface/wireless/print` | `mode=ap-bridge`, your SSID | `mode=station` → it is hunting for someone else's network, not serving one |
| `/interface/bridge/port/print` | `wlan1` listed, no `I` | Missing → `/interface/bridge/port/add bridge=bridgeLocal interface=wlan1` |

**Stop here and join it from a phone.** It should get a `192.168.88.x` lease
and browse, with **no portal**.

| Check | Expect | If it is not that |
|---|---|---|
| `/interface/wireless/registration-table/print` | the phone | Absent → it never associated; wrong SSID, or the radio is not running |
| `/ip/dhcp-server/lease/print` | a `192.168.88.x` for it | Absent → the DHCP server is not on this bridge → re-check stage 2 |
| browsing on the phone | works | Lease but no internet → NAT or the forward rules → stages 2 and 3 |

Do not go on until a phone reaches the internet over WiFi. Once the hotspot
exists, a phone that cannot get online is ambiguous between a radio fault and a
portal fault, and you will look in the wrong place.

### 5B. The access point — boards with no radio

Nothing to type on the MikroTik. The hotspot still runs on `bridge` and
captures every port in it, so an AP plugged into ether2–8 puts its wireless
clients behind the portal automatically — **provided the AP is bridging and not
routing.**

That proviso is the whole of this stage, and getting it wrong is the most
expensive mistake in this document.

On the AP:

| Setting | Value | Why |
|---|---|---|
| Mode | **Bridge / Access Point** — not Router, not WISP, not Repeater | See below |
| DHCP server | **off** | Leases must come from the MikroTik, or clients land on the wrong subnet and never meet the hotspot |
| LAN address | static, outside the pool — `192.168.88.2` | The pool starts at `.10`; you still need to reach its admin page |
| WiFi security | **open, no WPA** | Subscribers authenticate with a voucher. A WiFi password is a second secret for one purchase |
| Client isolation / AP isolation | **on** | The MikroTik's `default-forwarding=no` cannot help you — that traffic never leaves the AP |
| Uplink | LAN port to the MikroTik, **not** the AP's WAN port | A cable in the WAN port makes it route no matter what the mode says |

**An AP left in router mode NATs every client behind its own single address and
MAC.** The hotspot then sees *one device*. One voucher logs in and the entire
site is online under it; device binding counts one; all usage for every
customer accrues to one session. And it tests perfectly, because you test with
one phone. This is not a subtle degradation — it is the billing not working at
all, presenting as everything working.

**Then join the SSID from a phone and check the lease, before creating the
hotspot:**

| Check | Expect | If it is not that |
|---|---|---|
| the phone's IP | `192.168.88.x` | `192.168.0.x`, `192.168.1.x`, or anything else → **the AP is still routing.** Fix that before going on |
| `/ip/dhcp-server/lease/print` on the MikroTik | the phone, by its own MAC | Absent → the AP is not bridging to this segment |
| a second phone | a **second** lease, a different MAC | Only one lease for two phones → routing again, and the one you will not notice |
| browsing | works, **no portal** | Lease but no internet → NAT or forward rules → stages 2 and 3 |

That "second phone" row is the one worth doing. A single device cannot tell
bridging from routing, and every later symptom of getting it wrong looks like a
billing bug rather than a cabling one.

### 6. The hotspot

```
/ip/hotspot/setup
```

Answer `bridgeLocal`, certificate `none`, dns `8.8.8.8,1.1.1.1`, dns name
**`login.hotspot`** (exactly — it is what the CORS pattern matches), user
`admin` + a real password. Then check nothing was duplicated, and let yourself
past the portal:

```
/ip/hotspot/ip-binding/add mac-address=AA:BB:CC:DD:EE:FF type=bypassed comment="admin laptop"
/ip/hotspot/print
/ip/dhcp-server/print
/ip/pool/print
/ip/hotspot/profile/print
```

| Check | Expect | If it is not that |
|---|---|---|
| `/ip/hotspot/print` | one server, **no `I` flag** | `I` = *inactivated, not allowed by device-mode* → **stage 4**, and everything downstream is doing nothing |
| `/ip/dhcp-server/print` | **one** server on the bridge | Two → setup made its own → `/ip/dhcp-server/remove <the new one>`. Two on one segment hand out conflicting leases |
| `/ip/pool/print` | one pool | Two → remove the one no server references |
| `/ip/hotspot/profile/print` | `dns-name="login.hotspot"` | Anything else → `/ip/hotspot/profile/set hsprof1 dns-name=login.hotspot`. It must match `^https?://([\w-]+\.)?hotspot(:\d+)?$` or every API call is refused by the browser while your server logs clean 200s |
| `/ip/hotspot/profile/print` | `hotspot-address=192.168.88.1` | Wrong → you answered the interface prompt with the wrong bridge; `/ip/hotspot/remove [find]` and run setup again |

### 7. Walled garden — both rules

```
/ip/hotspot/walled-garden/add dst-host=api.example.com comment="Billing API"
/ip/hotspot/walled-garden/ip/add dst-address=SERVER_IP action=accept comment="Billing API direct"
/ip/hotspot/walled-garden/print
/ip/hotspot/walled-garden/ip/print
```

| Check | Expect | If it is not that |
|---|---|---|
| `/ip/hotspot/walled-garden/print` | the host rule, **no `inactivated` comment** | `;;; inactivated, not allowed by device-mode` → `proxy` is off → **stage 4**. The host rules run through the router's HTTP proxy |
| `/ip/hotspot/walled-garden/ip/print` | **one** entry for your server | Two identical → you ran it twice → `/ip/hotspot/walled-garden/ip/remove 1` |
| `HITS` after a phone loads the portal | non-zero | Still `0` → nothing matched; the portal cannot reach your API at all. Check `API_BASE` in `config.js` and that `api` is grey-clouded — behind Cloudflare the address rule stops matching |

### 8. Portal files

WinBox → **Files** → double-click **into** `hotspot` → drag the seven in.
Dropped at the root they are never served.

```
/file/print where name~"hotspot/"
```

| Check | Expect | If it is not that |
|---|---|---|
| `/file/print where name~"hotspot/"` | your seven, all under `hotspot/` | Named `config.js` not `hotspot/config.js` → they landed at the root and are never served → drag them **into** the folder |
| the sizes | `login.html` ≈33 KB, `md5.js` ≈8.8 KB | ~4 KB and ~7 KB are MikroTik's own — yours did not overwrite them, or `reset-html` has been run since |
| the phone | **your** portal, operator's name, packages listed | Stock MikroTik page → files at the root, or a cached page; forget the network and rejoin |
| | | Portal but **no packages** → the walled garden → **stage 7**, and check `HITS` |
| | | Portal but no business name → the token is wrong; check `TENANT_TOKEN` against Settings → Captive portal setup |

Leave `login-by=cookie,http-chap`. To *prove* the CHAP maths, strip both
fallbacks first — `cookie` lets later attempts replay the first, and adding
`http-pap` lets a broken hash succeed in plaintext:

```
/ip/hotspot/profile/set hsprof1 login-by=http-chap
/log/print follow where topics~"hotspot"
/ip/hotspot/active/remove [find mac-address="THE-PHONE-MAC"]
```

Expect `trying to log in by http-chap` then `logged in`, four times with a
fresh voucher each. `invalid username or password` every time → the portal
files are MikroTik's, not yours. Then put it back to `cookie,http-chap`.

### 9. Tunnel and API access

**Nothing to type.** Operator dashboard → **Routers → Add router**, leave *Set
up a management tunnel* ticked, and paste the block it gives you. It builds the
tunnel, enables the API, creates the user, adds the firewall rule and turns on
NTP. Then press **Test connection**. See §9.6.

The three failures it can report mean different things, and only one of them is
the password:

| Test connection says | It means | Run this |
|---|---|---|
| **timed out** | nothing at that address — the paste did not happen, or the tunnel is down | `/ping 10.10.0.1 count=3` on the router. No reply → `/interface/wireguard/peers/print` and check `endpoint-address` resolves and `persistent-keepalive=25s` is set |
| **connection refused** | reachable, but the API is off or on another port | `/ip/service/print where name=api` — expect `disabled: no`, `port: 8728`, `address: 10.10.0.1/32` |
| **invalid user name or password** | everything works except the credential | `/user/print detail where name=<yours>` — expect `group=full`, `address=10.10.0.1/32`. RouterOS never shows a password back, so set a new one and paste it into both sides from the same clipboard |

Ping working but Test connection timing out is the firewall: the accept rule
must sit **above** the `!LAN` drop, and the hotspot's dynamic rules shift every
index — check with `/ip/firewall/filter/print where !dynamic`.

### 10. Prove it

```
/ping 10.10.0.1 count=3
/log/print follow where topics~"hotspot"
```

From a phone that is *not* bypassed:

| Step | Expect | If it is not that |
|---|---|---|
| open any `http://` site | your portal, operator's business name | see **stage 8** |
| packages listed | prices and support numbers | walled garden → **stage 7** |
| redeem a voucher | `logged in` in the log, phone online | `invalid username or password` → CHAP; **stage 8** |
| WiFi off and on | reconnects without re-entering the code | asks again → `login-by` lost its `cookie` |
| dashboard | router shows **online** within 2 minutes | still offline → **stage 9** |

Watching the server at the same time removes all ambiguity:

```bash
docker compose logs web --since 5m | grep hotspot
```

Nothing at all → walled garden. Requests arriving with 4xx → token or tenant.
500 → read the traceback; it is logged now.

---

## Before anything

Settle these first; each has lead time and two of them gate everything else.

| | Why it blocks you |
|---|---|
| A domain | The M-Pesa callback and every router's walled-garden rule point at it |
| A payment method that works | Hetzner needs a card; M-Pesa GlobalPay is a prepaid virtual card and registrars and hosts reject it. A Kenyan registrar takes M-Pesa for the domain, but the server has no such route |
| Business registration | Safaricom will not issue a production shortcode to an individual. Longest lead time of anything here |

**Ask each operator one question before promising anything.** On their router:

```
/ip address print
/tool traceroute 8.8.8.8 count=1
```

If the WAN address is private, or hop 2–3 is `100.64–100.127` or `10.x`, they
are behind CGNAT and **your server cannot dial their router**. Safaricom and
Starlink residential both do this. That is not a blocker — Part 8 solves it —
but you must know before you plan around a public IP.

---

## Part 1 — Domain and DNS

1. Register the domain. A Kenyan KeNIC-accredited registrar takes **M-Pesa**;
   an international one needs a card. Either sells `.com`.
2. Cloudflare → **Add a domain** → **Free** plan → copy the two nameservers.
3. At the registrar, replace its nameservers with Cloudflare's.
4. **Check DNSSEC is off at the registrar** before switching. If the registry
   still publishes the old key while Cloudflare answers, validating resolvers
   reject every answer and the domain stops resolving — for some users and not
   others, which is the worst way to fail.
5. Delete any `*` wildcard records the registrar left behind. A wildcard makes
   `api.example.com` resolve to the wrong place before you create it, and
   Caddy's certificate challenge then goes somewhere else.
6. Keep the `0 issue "letsencrypt.org"` CAA record. Without it Caddy can never
   get a certificate, no matter what else is right.

Then, still in Cloudflare:

- **SSL/TLS → Full (strict)**, chosen explicitly rather than left on automatic.
  The automatic scan can settle on *Flexible*, which combined with Django's
  `SECURE_SSL_REDIRECT` is an infinite redirect loop that appears weeks later.
- **Edge Certificates → Always Use HTTPS → on**
- **Under Attack Mode → off.** It challenges unattended requests, and an M-Pesa
  callback is exactly that — a challenged callback is a payment that never
  confirms.
- **Email Routing** → verify a destination, add `admin@example.com`, accept the
  automatic MX and SPF records. This is your non-free address for the Hetzner
  signup.

Verify from outside your own network:

```bash
nslookup -type=NS example.com 8.8.8.8
nslookup -type=MX example.com 8.8.8.8
```

---

## Part 2 — The server

**Hetzner account:** sign up with `admin@example.com`, **VPN off**, name and
address exactly as on your ID. Verify by **ID document + selfie** (physical
document, live photo — screenshots are rejected). Weekday, 10:00–18:00 EAT, so
a manual review lands the same day.

Use **Individual** unless you already hold a certificate of incorporation;
picking Organisation without papers stalls verification. It converts later.

**Create the server:**

| Field | Value |
|---|---|
| Location | Falkenstein or Nuremberg |
| Image | Ubuntu LTS |
| Type | **CX33** (4 vCPU, 8 GB, 80 GB) or **CAX21** on the Arm tab |
| Networking | **Public IPv4 ticked** |
| Backups | **enabled** (+20%) |
| SSH key | your `id_ed25519.pub` |
| Name | `smartbill-prod-1` |

**Do not skip the IPv4.** `MPESA_TRUSTED_IPS` is five IPv4 addresses because
that is what Safaricom sends callbacks from.

Arm is cheaper and works — `cryptography` and `psycopg[binary]` both publish
`aarch64` wheels and all three base images publish arm64 — but x86 removes the
question entirely.

### Harden it, before Docker

```bash
ssh root@SERVER_IP

adduser --disabled-password --gecos deploy deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys

ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable
```

Set a password for `deploy` — needed for `sudo`, not for SSH:

```bash
passwd deploy
```

**Prove `deploy` works in a second session before locking anything.** Then:

```bash
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' \
  > /etc/ssh/sshd_config.d/99-hardening.conf
sshd -t && systemctl reload ssh
```

Add `PermitRootLogin no` to that file **at the end of the build**, not now —
the remaining steps need root, and with root closed each one needs an
interactive `sudo`.

Also switch on Hetzner's **Cloud Firewall** (22, 80, 443). It sits outside the
machine and holds even when something on the box is wrong.

---

## Part 3 — The backend

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
```

As `deploy`:

```bash
cd /home/deploy
git clone https://github.com/ankomark/billing-system.git billing
cd billing/backend
```

Write `.env` — generate every secret on the server so it never travels:

```bash
cat > .env <<EOF
DEBUG=False
ENVIRONMENT=production
SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n=')
FIELD_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')

ALLOWED_HOSTS=api.example.com
CORS_ALLOWED_ORIGINS=https://app.example.com
PLATFORM_BASE_URL=https://api.example.com

POSTGRES_DB=wifi_billing
POSTGRES_USER=wifi_app
POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=\n')
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=\n')

REDIS_URL=redis://redis:6379
GUNICORN_WORKERS=4
FLOWER_BASIC_AUTH=admin:$(openssl rand -base64 18 | tr -d '/+=\n')

MPESA_ENV=sandbox
MPESA_ALLOW_LOCAL_CALLBACK=False

SENTRY_DSN=
AT_USERNAME=
AT_API_KEY=
EOF
chmod 600 .env
```

Four of those lines are where the guide used to be wrong, and each fails at a
different stage:

- **`PLATFORM_BASE_URL` must be the API host.** The M-Pesa callback is built
  from it. Point it at the dashboard and customers pay, Safaricom POSTs into a
  404, and nothing on your server logs anything because nothing was contacted.
- **`REDIS_URL` takes no database number.** The app appends its own, so a
  trailing `/0` becomes `redis://redis:6379/0/0` and kombu refuses it. Web
  comes up fine; worker and beat crash-loop.
- **`POSTGRES_DB=wifi_billing`** — compose passes that name to the image.
- **`POSTGRES_ADMIN_PASSWORD` and `FLOWER_BASIC_AUTH` are required**; compose
  aborts without them.

Then:

```bash
docker compose up -d --build --wait
docker compose ps
curl -s http://localhost:8000/health/
```

**Copy `.env` into your password manager now.** `FIELD_ENCRYPTION_KEY` encrypts
every router password; a database restored without it leaves them unreadable
blobs and the platform comes up healthy and reaches no routers.

Confirm tenant isolation is real:

```bash
docker compose exec -T postgres psql -U wifi_admin -d wifi_billing -tAc \
  "select rolname, rolsuper, rolbypassrls from pg_roles where rolname='wifi_app';"
```

`wifi_app | f | f` — if `rolbypassrls` is `t`, every RLS policy is inert while
still listed in `pg_policies` exactly as if it worked.

---

## Part 4 — TLS

Add in Cloudflare: **A record `api` → SERVER_IP, grey cloud (DNS only)**.

Grey first. Cloudflare's proxy intercepts Let's Encrypt's challenge and Caddy
never gets a certificate.

```bash
apt-get install -y caddy
cat > /etc/caddy/Caddyfile <<'EOF'
api.example.com {
	reverse_proxy localhost:8000
}
EOF
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
journalctl -u caddy -n 20 | grep -i certificate
```

Verify from outside, then flip `api` to **orange**:

```bash
curl https://api.example.com/health/
```

Between `docker compose up` and Caddy being ready, the app redirects everything
to `https://` — that is `SECURE_SSL_REDIRECT`, waiting for the proxy. `/health/`
is exempt, which is why `--wait` succeeded.

Diarise the certificate renewal around day 60. It has to reach Caddy through
Cloudflare's proxy; it normally does, and switching `api` back to grey for an
hour fixes it if not.

---

## Part 5 — The frontend

Vercel → **Add New → Project** → import the repo.

| Setting | Value |
|---|---|
| Root Directory | **leave as `./`** |
| Framework Preset | Other |
| Build / Output / Install | blank |

`vercel.json` at the repo root supplies the build. Setting Root Directory to
the frontend folder means Vercel looks for `vercel.json` *inside* it, doesn't
find it, and deploys nothing — a three-second build and a 404.

Environment variable:

```
REACT_APP_API_URL = https://api.example.com/api/
```

The `/api/` path and the trailing slash both matter — every call appends a
relative path onto it. Set it **before** the first deploy: Create React App
bakes these in at build time, so changing it later needs a redeploy, not a
restart.

Then **Settings → Domains → `app.example.com`**, take the CNAME target it
gives you, and add it in Cloudflare on **grey cloud**. Vercel runs its own CDN
and certificate; proxying it through Cloudflare as well breaks the handshake.

Confirm the API URL really landed in the bundle rather than assuming:

```bash
curl -s https://app.example.com/ | grep -o 'main\.[a-f0-9]*\.js'
curl -s https://app.example.com/static/js/main.XXXX.js | grep -c 'api.example.com'
```

---

## Part 6 — Backups

```bash
openssl rand -base64 36 | tr -d '\n' > /home/deploy/.backup-pass
chmod 600 /home/deploy/.backup-pass
mkdir -p /home/deploy/backups
```

`/home/deploy/backup.sh` — the version in `GUIDE.md` §1.7, with two things that
matter:

- **Dump as `wifi_admin`, the superuser.** `wifi_app` is `NOBYPASSRLS`, and RLS
  applies to `pg_dump`'s SELECTs exactly as it does to Django's. Dumped as the
  app role you get the schema and almost none of the rows, at a plausible file
  size, and find out when you restore.
- **Refuse to ship a dump under 10 KB.** gzip of nothing is still valid gzip,
  so an empty backup fails nothing and quietly fills the retention window.

```bash
chmod 700 /home/deploy/backup.sh
( crontab -l 2>/dev/null; echo "15 2 * * * /home/deploy/backup.sh >> /home/deploy/backup.log 2>&1" ) | crontab -
/home/deploy/backup.sh
```

**Then restore one, this week.** Decrypt, load into a scratch database, and
count tables, policies and rows. An untested backup is a belief.

Store `.backup-pass` in your password manager, **not** in the bucket holding
the dumps. Configure `rclone` against Cloudflare R2 or Backblaze B2 —
`backup.sh` switches to off-site automatically once a remote exists.

---

## Part 7 — Platform owner and first operator

```bash
docker compose exec web python manage.py create_platform_owner --username you
```

Don't pass `--password` on the command line; it prompts, and the flag would
leave it in shell history.

Log in at `https://app.example.com/login`. **Not the `*.vercel.app` URL** — that
origin isn't in `CORS_ALLOWED_ORIGINS`, and the failure presents as "invalid
username or password" because the browser discards a response your server
returned quite happily.

**Platform → Operators → New Operator.** `name`, `admin_username` and
`admin_password` are required; `business_name` is what subscribers see on the
portal. It creates the operator and its login in one transaction.

Then log in as that operator and:

1. **Create packages first.** A portal with no packages looks identical to one
   that cannot reach your API. Check `duration_value` and `duration_unit` — a
   package named "1hr" configured as 5 minutes sells five minutes.
2. **Settings → Captive portal setup** — copy the **tenant token**. This is what
   goes on the router.

---

## Part 8 — The tunnel, for CGNAT routers

Skip only if the operator has a genuine public IP. Most Kenyan operators do not.

Add in Cloudflare: **A record `vpn` → SERVER_IP, grey cloud.** Cloudflare cannot
proxy UDP; orange would blackhole it. The router points at the *name*, so
moving hosts is a DNS change rather than reconfiguring every router you have.

**On the server, once:**

```bash
apt-get install -y wireguard
cd /etc/wireguard && umask 077
wg genkey | tee server-private.key | wg pubkey > server-public.key
cat > wg0.conf <<CONF
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = $(cat server-private.key)
CONF
systemctl enable --now wg-quick@wg0
ufw allow 51820/udp
cat server-public.key
```

Open **UDP 51820** in the Hetzner Cloud Firewall too.

**Then install the peer watcher, also once.** This is what lets the Routers
page add a tunnel peer without anybody opening a terminal — see §9.6. The web
container writes a request into a spool directory and a systemd path unit out
here applies it, so Django never needs root or the host's network namespace:

```bash
cd /opt/billing/backend/docker
install -m 0755 wg-peer-watcher.sh /usr/local/bin/wg-peer-watcher
install -d -m 0770 -o deploy -g deploy /var/spool/wg-requests
cp wg-peer-watcher.path wg-peer-watcher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wg-peer-watcher.path
```

**Finally, put four values in `backend/.env`** and redeploy. Without them the
Routers page refuses to provision rather than emitting a script that pastes
cleanly and produces a tunnel that never comes up:

```bash
WG_SERVER_PUBLIC_KEY=$(sudo cat /etc/wireguard/server-public.key)
WG_ENDPOINT_HOST=vpn.example.com
WG_TUNNEL_SUBNET=10.10.0.0/24
WG_SERVER_TUNNEL_IP=10.10.0.1
```

That is the whole of the tunnel setup. **Per-router work is done from the
dashboard from here on** — §9.6.

Two constants worth understanding, because they are baked into the generated
script and someone will eventually want to change them:

`allowed-address=10.10.0.1/32`, not `0.0.0.0/0`. The tunnel carries management
traffic only; routing a hotspot's browsing through a 600 MHz MIPS CPU doing
software crypto, via Germany, is not what you want.

`persistent-keepalive=25s` is what makes CGNAT work at all — the NAT mapping
expires in seconds, and your server can only reach back through one the router
is holding open.

> **If you ever do this by hand**, take the router's public key with
> `:put [/interface/wireguard/get [find name=wg-smartbill] public-key]` — **not**
> `/interface/wireguard/print`, which on 7.19 prints `private-key=` in plain
> sight alongside it. A private key read into a terminal you are pasting from
> is a private key you have to rotate.

---

## Part 9 — The MikroTik

### 9.1 The board itself — device mode, and the clock

**Check before you do anything.** On 7.13+ some features need someone
physically present to enable them, but which ones depends on the shipped mode:

```
/system/device-mode/print
```

A board in `mode: home` — which is how an RB951 arrives — already reads
`hotspot: yes`, and there is nothing to do. Sending an operator to pull the
power on a router that was never blocked wastes a site visit.

What that same output will read is `proxy: no`, and **you need `proxy` as
well**. The `dst-host` walled-garden entries in §9.4 are served through the
router's HTTP proxy; without it they are created, they appear in `print`, and
they carry the comment `inactivated, not allowed by device-mode` while doing
nothing. The IP entries in `/ip/hotspot/walled-garden/ip` are firewall-based
and work regardless, which is why a portal can look fine until the day a phone
with Private DNS needs the hostname rule.

So if either flag is `no`:

```
/system/device-mode/update hotspot=yes proxy=yes
```

Name both. `update` sets what you pass it and leaves the rest.

**Then pull the power and leave it off for ten seconds.** A software reboot does
not count. You have about five minutes. Verify afterwards — `/system/device-mode/print`
should show both as `yes`, and the `inactivated` comments should be gone from
`/ip/hotspot/walled-garden/print`. If the window lapsed, run the update again
and be quicker.

Enable only what you need, not `mode=enterprise`, which turns on container,
scheduler, socks, fetch and proxy together.

**Then set the clock, before anything starts selling time.**

```
/system/ntp/client/set enabled=yes servers=time.cloudflare.com,pool.ntp.org
/system/clock/set time-zone-name=Africa/Nairobi
/system/ntp/client/print
```

You want `enabled: yes` and `status: synchronized`.

These boards have no battery-backed clock. Every power cut puts the time back
to whatever the firmware defaults to — observed on this build: a ten-second
power cycle left the router two hours out, silently. The router's clock is what
decides when a subscriber's hour is up, so a router that disagrees with your
server holds sessions open past expiry or cuts them short, and produces billing
disputes that cannot be reconstructed afterwards. In a market where the power
blinks daily, this is not a nicety.

NTP needs the WAN up, so a cold boot runs on a wrong clock for a few seconds
before it syncs. That is fine — the point is that it converges rather than
staying wrong indefinitely.

### 9.2 The radio, or the access point

A hotspot captures the interface it runs on. If the WiFi is not on that
interface, or is not broadcasting at all, the hotspot works perfectly and no
customer can reach it.

Which half of this section applies depends on the board:

```
/interface/print
```

`wlan1` present → **§9.2a**. Ether ports only, as on an RB5009UG+S+ or an
RB4011 → the board has no radio at all, and the WiFi is a separate access
point → **§9.2b**. On such a board `/interface/wireless/print` answers *no such
command*; the package is absent because the hardware is, and that is not a
fault to chase.

#### 9.2a A board with its own radio

Check before you build anything on top:

```
/interface/print
/interface/wireless/print
/interface/bridge/port/print
```

Three faults are common on a board that has been deployed before, and they
present identically — the SSID simply does not exist, with nothing in the log:

- **`X` on `wlan1`.** The radio is disabled.
- **`mode=station`.** It is configured as a *client*, hunting for someone
  else's network rather than serving one.
- **`;;; managed by CAPsMAN`.** A controller owns the interface. If it points
  at a controller that isn't there — `/interface/wireless/cap/print` showing
  `enabled: yes` with an empty `caps-man-addresses` — the radio stays down
  forever, and any local setting you apply is discarded.

Release it first, or nothing below sticks:

```
/interface/wireless/cap/print
/caps-man/manager/print
/interface/wireless/cap/set enabled=no
```

Re-print and confirm the `managed by CAPsMAN` comment is gone. For a single
standalone router CAPsMAN buys nothing and costs you an afternoon.

Then make it an access point:

```
/interface/wireless/security-profiles/set default mode=none

/interface/wireless/set wlan1 mode=ap-bridge ssid="OPERATOR-SSID" band=2ghz-b/g/n \
  channel-width=20mhz frequency=2437 security-profile=default \
  default-forwarding=no country=kenya disabled=no

/interface/bridge/port/add bridge=bridgeLocal interface=wlan1
```

**`mode=none` — open, no WPA.** Deliberate. Subscribers authenticate at the
portal with a code; a WiFi password would mean two secrets for one purchase and
make the voucher pointless.

**`default-forwarding=no`** isolates wireless clients from each other. On an
open network without it, every customer's device is directly reachable by every
other customer on the same radio.

**`channel-width=20mhz` and a fixed 1 / 6 / 11.** In a congested 2.4GHz band
40MHz costs more in retransmits than it gains, and `frequency=auto` re-picks at
boot — sometimes onto a neighbour.

**Prove the radio carries plain traffic before you go on.** A phone should join
the SSID, take a `192.168.88.x` lease and browse, with no portal:

```
/interface/wireless/registration-table/print
/ip/dhcp-server/lease/print
```

Once the hotspot exists, a phone that cannot reach the internet is ambiguous
between a radio fault and a portal fault, and you will look in the wrong place.

#### 9.2b A board with no radio, and a separate AP

There is nothing to configure on the MikroTik. The hotspot binds to the bridge
and captures every port in it, so an access point plugged into any bridged
ether port puts its wireless clients behind the portal without the router
knowing or caring that they arrived over WiFi.

All of the risk moves to the AP, and it concentrates in one setting.

**The AP must bridge, not route.** Mode *Access Point* or *Bridge*; its own DHCP
server off; a static address outside the pool — `192.168.88.2`, since the pool
starts at `.10` — so its admin page stays reachable; open WiFi with no WPA,
because the voucher is the credential; and its uplink cable in a **LAN** port,
never the WAN port, which makes most consumer APs route regardless of the mode
they claim.

Consumer APs ship in router mode. An AP left that way NATs every wireless
client behind one address and one MAC, and the consequences are not
proportional to the mistake:

- The hotspot sees **one device**. One voucher authenticates it, and everyone
  on that SSID is online under that one session.
- Device binding counts one device, so a one-device package covers the site.
- All usage accrues to one customer; every other customer records nothing.
- `collect_hotspot_usage` and the tethering sweep are both reading a single
  session that is genuinely carrying a building's worth of traffic.

None of that raises an error anywhere. It presents as the platform working.

**And it tests clean with one phone**, which is how it survives commissioning.
The check that catches it is two phones and two leases:

```
/ip/dhcp-server/lease/print
```

Two devices on the SSID must produce **two** leases with **two** MACs, both
`192.168.88.x`. One lease for two phones is the AP routing. A lease in some
other subnet — `192.168.0.x`, `192.168.1.x` — is the same fault seen earlier.

**Client isolation belongs on the AP too.** `default-forwarding=no` in §9.2a is
what stops customers reaching each other's devices on an open network, and it
has no equivalent here: traffic between two clients of the same AP is switched
inside the AP and never reaches the MikroTik. Turn on whatever that vendor
calls it — *Client Isolation*, *AP Isolation*, *Guest Mode*.

Prove a phone browses with **no portal** before creating the hotspot, exactly
as in §9.2a and for exactly the same reason: afterwards, a phone that cannot
reach the internet is ambiguous between an AP fault and a portal fault.

One consolation for the extra box: a radio-less board is usually a much larger
router. An RB5009's four ARM64 cores at 1 GB of RAM will carry a hotspot an
RB951 could not, and removing fasttrack costs it nothing measurable. Splitting
free ports off (§9.7) is also more useful there, since such boards come with
seven or more LAN ports and no radio competing for them.

### 9.3 Create the hotspot

```
/ip/hotspot/setup
```

| Prompt | Answer |
|---|---|
| hotspot interface | the bridge — **check its name** |
| local address / masquerade / pool | accept defaults |
| select certificate | `none` |
| smtp server | accept |
| dns servers | `8.8.8.8,1.1.1.1` |
| **dns name** | **`login.hotspot`** |
| local hotspot user | `admin` + a real password |

**The bridge is not always called `bridge`.** An RB951 ships it as
`bridgeLocal`. Read the name off `/interface/bridge/print` and use that one,
here and everywhere in §9.7 — a hotspot bound to an interface that doesn't
exist is a prompt you cannot get past, and one bound to the *wrong* bridge
captures nothing.

If a DHCP server already exists on that interface, the setup detects it and
skips the address and pool prompts. Fewer questions than this table is normal.
Check afterwards that it reused them rather than adding its own:

```
/ip/dhcp-server/print
/ip/pool/print
```

One server and one pool on that interface. Two DHCP servers on one segment hand
out conflicting leases, and the intermittent failures that follow look nothing
like a DHCP problem.

**The DNS name is not cosmetic.** The portal's origin becomes that name, and
`settings.py` allows portal origins by pattern — RFC1918 addresses and
`^https?://([\w-]+\.)?hotspot(:\d+)?$`. `login.hotspot` matches. `login.acme`
matches nothing, and every API call is refused by the browser while your server
logs healthy 200s.

Your own machine is now behind the portal. Bypass it while you work:

```
/ip/dhcp-server/lease/print
/ip/hotspot/ip-binding/add mac-address=YOUR-MAC type=bypassed comment="admin laptop"
```

### 9.4 Walled garden — the one that breaks portals

```
/ip/hotspot/walled-garden/add dst-host=api.example.com comment="Billing API"
/ip/hotspot/walled-garden/ip/add dst-address=SERVER_IP action=accept comment="Billing API direct"
```

The portal must reach your API **before** the customer has logged in — that is
the entire point. Miss it and the page loads, shows no packages, and nothing on
the router explains why.

**Add both rules.** The hostname rule works by watching DNS: the router sees a
client look `api.example.com` up, notes the answer, and permits that address.
Reboot the router and those dynamic permissions are gone — and if the phone
answers from its own cache, or uses private DNS (Android's *Private DNS*
defaults to Automatic, which attempts DNS-over-TLS), the router never sees a
lookup and never permits anything. The portal then loads and every API call
fails with a bare network error. Observed on a live router: after a reboot the
rule sat at `hits: 0` while the config was unchanged and correct.

The address rule does not depend on observing DNS, so it survives that.

> **The address rule is only valid while `api` is grey-clouded.** Flip it to
> orange and requests go to Cloudflare's addresses instead, and this entry stops
> matching. Use Cloudflare's IP ranges, or rely on the hostname rule, if you
> proxy the API.

The `HITS` counter is a free diagnostic — still `0` after a phone has loaded the
portal means nothing matched, which is a different problem from a request that
matched and was refused.

### 9.5 API access, over the tunnel

```
/ip/service/set api disabled=no port=8728 address=10.10.0.1/32
/user/add name=billing password=STRONG group=full address=10.10.0.1/32 comment="SmartBill"
/ip/firewall/filter/add chain=input action=accept protocol=tcp dst-port=8728 \
  src-address=10.10.0.1 in-interface=wg-smartbill comment="Billing API via tunnel" place-before=0
```

The firewall rule is required **wherever an input firewall exists** — the
default configuration ends with a rule dropping input that did not arrive on
the `LAN` interface list, and `wg-smartbill` is not in that list. Ping still
works, because ICMP is accepted by an earlier rule, while TCP 8728 is silently
dropped. That combination reads as a wrong password.

Do not assume the drop is there. A board that shipped in bridge mode can have
an entirely empty filter table:

```
/ip/firewall/filter/print
```

If that returns nothing, the API will answer over the tunnel without any rule
at all — and the router has no input protection whatsoever, which is the larger
problem. Build the base set first, then add the tunnel rule above the final
drop:

```
/ip/firewall/filter/add chain=input action=accept connection-state=established,related,untracked
/ip/firewall/filter/add chain=input action=drop connection-state=invalid
/ip/firewall/filter/add chain=input action=accept protocol=icmp
/ip/firewall/filter/add chain=input action=drop in-interface-list=!LAN

/ip/firewall/filter/add chain=forward action=accept connection-state=established,related,untracked
/ip/firewall/filter/add chain=forward action=drop connection-state=invalid
/ip/firewall/filter/add chain=forward action=drop connection-state=new connection-nat-state=!dstnat in-interface-list=WAN
```

Check `/interface/list/member/print` shows the LAN bridge before you add that
fourth rule, or you lock yourself out of everything except WinBox-by-MAC.

**No fasttrack rule, deliberately.** MikroTik's stock configuration includes
one, and it must not be used on a hotspot router: fasttrack shunts established
connections past the firewall *and past hotspot accounting*, so the byte
counters stop incrementing. `collect_hotspot_usage` reads exactly those
counters, and every subscriber would appear to have used almost nothing.

The rule set above omits it. **A board that kept its defconf firewall still has
one**, and that is the likelier case, because a full default rule set is
exactly what makes you skip this stage:

```
/ip/firewall/filter/remove [find action=fasttrack-connection]
```

A factory reset restores it along with everything else, so this is a check to
repeat after any reset rather than a job done once per router.

Verify the tunnel rule landed above the `!LAN` drop rather than below it — the
hotspot inserts its own dynamic rules, which shifts every index:

```
/ip/firewall/filter/print where !dynamic
```

Restricting both the service and the user to `10.10.0.1/32` is stronger than
restricting to a public IP: a tunnel address is reachable only by something
already holding your server's private key, and it doesn't change when you
move hosts.

If the user already exists, `/user/add` fails — use `/user/set` instead.

**RouterOS never shows a password back.** `/user/print detail` lists the group,
the address restriction and the timeouts, and nothing else. If registration
later fails on credentials there is no way to compare the two sides — set a new
one and paste it into both from the same clipboard, rather than typing it twice
and trusting yourself.

### 9.6 Register it

**This replaces §9.5 and most of §8 for every router after the first.** If the
tunnel is set up on the server, you do not need §9.5's commands, an SSH
session, or the router's public key — the dashboard produces all of it.

Operator dashboard → **Routers → Add router**:

1. Leave **"Set up a management tunnel"** ticked. It is the right answer for
   anything behind an LTE box or a shared uplink, which is nearly everything.
   Untick it only for a router with a genuine public address.
2. Fill in the name, and the **API username and password you want created** —
   they do not exist on the router yet; the generated script creates them.
   Leave the IP address alone, it is allocated for you.
3. Press **Register & get setup commands**.
4. **Copy the block** and paste it into WinBox → New Terminal. It builds the
   tunnel, enables the API, creates the user, adds the firewall rule and turns
   on NTP.
5. Press **Test connection**.

Green means the whole chain works — tunnel, firewall, service, credentials.
The health sweep picks it up within two minutes.

**The block is shown once.** It carries the router's private key and the API
password, and the platform stores neither. Lose it and you register the router
again for a fresh one; it costs a paste, not a site visit.

Failures are worth reading precisely, because three different problems arrive
in the same red box:

| It says | It means |
|---|---|
| **timed out** | Nothing at that address. The paste did not happen, or the tunnel is not up — check `/ping 10.10.0.1 count=3` on the router |
| **connection refused** | Host reachable, API service off or on another port |
| **invalid user name or password** | Everything works except the credential. Set a new one and paste it into both sides from the same clipboard |

**Do not create user profiles by hand.** The backend builds and maintains
`HOTSPOT_PKG_<id>_D<devices>` and `PPPOE_PKG_<id>` from the package definition,
so a manual edit is overwritten the next time somebody buys.

### 9.7 Optional — free ports alongside the paid hotspot

For an operator who wants their own desks, a till or an office PC online
without paying: give those ports a separate bridge. The hotspot captures
whatever interface it runs on, so the fix is to take ports *out* of `bridge`
rather than to configure the hotspot.

This example frees **ether3** and **ether4** and leaves ether2, ether5 and the
WiFi behind the portal. It writes the hotspot's bridge as `bridge`; on an RB951
that is `bridgeLocal`, so substitute the name you read off
`/interface/bridge/print` — see §9.3.

**Do not run this from a machine plugged into ether3 or ether4** — you lose the
lease mid-change. Use WiFi, another port, or connect WinBox by MAC address.

```
/interface/bridge/port/remove [find interface=ether3]
/interface/bridge/port/remove [find interface=ether4]

/interface/bridge/add name=bridge-free comment="Free / unmetered ports"
/interface/bridge/port/add bridge=bridge-free interface=ether3
/interface/bridge/port/add bridge=bridge-free interface=ether4

/ip/address/add address=192.168.99.1/24 interface=bridge-free
/ip/pool/add name=free-pool ranges=192.168.99.10-192.168.99.254
/ip/dhcp-server/add name=free-dhcp interface=bridge-free address-pool=free-pool disabled=no
/ip/dhcp-server/network/add address=192.168.99.0/24 gateway=192.168.99.1 dns-server=8.8.8.8,1.1.1.1

/interface/list/member/add list=LAN interface=bridge-free
```

**That last line is the one people forget.** RouterOS's default firewall
accepts input from the `LAN` interface list and drops the rest, so without it
the ports get addresses and then have their DNS and DHCP dropped by the router
itself. The symptom is "connected, no internet" — identical to a hotspot fault,
and it sends you looking in the wrong place.

No NAT rule is needed: the `defconf: masquerade` rule matches
`out-interface-list=WAN` and covers any new subnet.

Nothing about the hotspot changes. It stays bound to `bridge`, which now holds
only the interfaces you charge for.

Two additions worth making at the same time:

```
# The free side should not reach paying customers' devices
/ip/firewall/filter/add chain=forward action=drop in-interface=bridge-free \
  out-interface=bridge comment="free ports cannot reach hotspot LAN"

# Nothing else limits these ports
/queue/simple/add name=free-cap target=192.168.99.0/24 max-limit=5M/5M comment="free ports"
```

The cap matters more than it looks. These ports bypass the hotspot entirely —
no session, no accounting, no package limit, no expiry — so one person on a
free port can saturate the uplink every paying customer is sharing.

**Verify:**

```
/interface/bridge/port/print
/ip/dhcp-server/print
```

A laptop on ether3 should get a `192.168.99.x` address and browse with **no
portal**. On ether2 or the WiFi, the portal appears as before.

**Devices on free ports are invisible to the platform** — no customer record,
no usage figures, no tethering detection. That is the point, but it also means
an operator cannot see who is consuming that bandwidth. Tell them which ports
are which before they wonder where their capacity went.

---

## Part 10 — The portal files

Edit **`config.js` only**:

```js
var API_BASE     = 'https://api.example.com/api';   // no trailing slash
var TENANT_TOKEN = 'TENANT_TOKEN';
```

Everything else — business name, packages, prices, support numbers — comes from
that operator's settings and follows automatically.

Upload **all seven files** into the router's existing `hotspot` folder, not a
new one: `login.html`, `alogin.html`, `status.html`, `logout.html`, `config.js`,
`md5.js`, `smartbill.png`.

WinBox → **Files** → double-click **into** `hotspot` → drag them in. Dropped at
the root they are never served, and the router keeps showing its own stock login
page as though the upload failed.

```
/file/print where name~"hotspot/"
```

Leave the profile on its default:

```
/ip/hotspot/profile/set hsprof1 login-by=cookie,http-chap
```

**CHAP, and not PAP, is the point.** The radio is open and the portal has no
certificate, so under PAP every voucher code crosses the air in cleartext and
anyone in range can harvest codes. CHAP sends `MD5(chap-id + code + challenge)`
over a challenge that is fresh each time, so a captured hash is worth nothing.
`cookie` is what lets a phone reconnect later without re-entering its code.

**Verifying the CHAP maths needs both of those turned off**, or the test proves
nothing: `cookie` lets attempts after the first replay a session rather than
authenticate, and adding `http-pap` lets a broken hash fall through to
plaintext and succeed anyway. So, for the test only:

```
/ip/hotspot/profile/set hsprof1 login-by=http-chap
```

Redeem a fresh voucher three or four times, clearing the session between
attempts so each is a real exchange:

```
/ip/hotspot/active/remove [find mac-address="THE-PHONE-MAC"]
/log/print follow where topics~"hotspot"
```

You want `trying to log in by http-chap` followed by `logged in`, every time.
Then put it back to `cookie,http-chap`.

---

## Part 11 — Prove it

From a phone on that WiFi — not a bypassed device:

1. The portal appears, with the **operator's** business name, not "WiFi"
2. **Packages are listed.** If not, it is the walled garden. Check that first.
3. Support numbers appear beneath them
4. Issue a voucher from the operator's **Dashboard → Issue Voucher**
5. Enter it under **"Already paid? Use your code"**
6. **The phone goes online**
7. Turn WiFi off and on — it reconnects without re-entering the code
8. **On an external AP (§9.2b): a second phone must still be shown the
   portal.** If it is online already, riding the first phone's session, the AP
   is routing rather than bridging and one voucher is covering the whole site.
   Nothing else in this list detects that

Watch the server while you do it. It removes all ambiguity:

```bash
docker compose logs web --since 5m | grep hotspot
```

- **Nothing at all** → walled garden
- **Requests arriving, 4xx** → token or tenant
- **500** → read the traceback; it is logged now

---

## M-Pesa — when Safaricom issues credentials

Not done yet, and it needs the business registration.

1. Operator dashboard → **Settings**: consumer key, consumer secret, shortcode
   type, shortcode, store number (Buy Goods only), passkey

   **PayBill and Buy Goods are different products and the push has to name
   which.** A till carries two numbers doing different jobs — the *store
   number* signs the STK password and identifies the merchant, the *till* is
   where the money lands — and they are often, but not always, the same value.
   Sign with the wrong one and Safaricom accepts the request, returns
   `ResponseCode 0`, then fails it a second later with `ResultCode 2029` having
   delivered no prompt to the phone. Nothing names the field that was wrong,
   and **Test M-Pesa still passes**, because OAuth is fine.

   The settings page reports what a push will actually carry — business
   shortcode, party_b, transaction type — underneath the fields. Read it back
   rather than trusting what you typed; it is resolved by the same code that
   builds a real push.
2. Register **exactly** the callback URL shown on that same page —
   `https://api.example.com/api/mpesa/callback/<operator token>/`. The token is
   what makes the callback load the right operator's credentials.
3. Set `MPESA_ENV=production` in `.env` and redeploy
4. Test with the smallest package, with a real phone and real money

`MPESA_TRUSTED_IPS` in `settings.py` is five Safaricom IPv4 addresses. If
callbacks are rejected as untrusted after you put Cloudflare's proxy in front
of `api`, that check is reading the wrong client address.

---

## Shipping a change

```bash
ssh deploy@SERVER_IP /home/deploy/deploy.sh
```

Backup, pull, build, **migrate, then restart** — in that order. The migration
is its own step because `docker compose up` may leave an already-exited
one-shot container alone, silently skipping it and starting new code against
the old schema. Vercel redeploys the frontend on push by itself.

---

## Running the tests on the server

```bash
docker compose run --rm -T -e SECURE_SSL_REDIRECT=False \
  web python manage.py test billing --noinput
```

**That flag is not optional, and leaving it off is why the suite looks
catastrophically broken.** `settings.py` turns `SECURE_SSL_REDIRECT` on
whenever `DEBUG=False`, which is correct for serving traffic and wrong for the
test client: every request is answered with a 301 to its https form before it
reaches a view. Observed on this build — **308 failures and 222 errors, of
which 264 were literally `301 != 200`.** With the flag, the same commit
returned 9 and 5.

Half an hour can go into reading tracebacks that all say the same thing about
the environment and nothing about the code. Check the first failure for
`301 != ` before believing any of it.

Django builds its own `test_` database and drops it afterwards, so this does
not touch the operator data next to it. It takes eight to fourteen minutes.

**Run it detached.** `docker compose run` dies with the SSH session, so a
connection that drops takes the run with it and leaves a half-written log:

```bash
setsid nohup docker compose run --rm -T -e SECURE_SSL_REDIRECT=False \
  web python manage.py test billing --noinput > /tmp/testrun.log 2>&1 &
```

Then watch it with `tail -f /tmp/testrun.log`, and check whether it is still
going with `docker ps --filter name=backend-web-run`.

To narrow down to what you are working on, name the classes:

```bash
docker compose run --rm -T -e SECURE_SSL_REDIRECT=False \
  web python manage.py test --noinput billing.tests.TetheringSweepTests
```

---

## Open items

- **Off-site backups.** Local only until `rclone` has a remote — the dumps
  currently share a disk with the database.
- **`PermitRootLogin no`** once no further root work is expected.
- **Android MAC randomisation.** Device binding is by MAC, and modern Android
  randomises per SSID by default — seen on the first test phone of this build,
  a Galaxy presenting `3E:5E:…` with the locally-administered bit set. A
  customer who toggles WiFi looks like a new device and can exhaust a
  one-device package. Not fixable on the router; it needs a decision about what
  identity a "device" is. When testing, set the phone to **Use device MAC** so
  you are not chasing this by accident.
- **Package hygiene.** Check every package's `duration_value`/`duration_unit`
  against its name, and that `is_hotspot` is set correctly — a PPPoE package on
  a hotspot portal sells something the router cannot deliver.

---

## Debug

### The method

Almost every failure here presents as a different layer than the one that is
broken. A CORS refusal reads as "invalid username or password". A crash in the
router code reads as "check you pasted the whole message". A router with no
internet reads as a hotspot fault. **Find the layer before changing anything**,
or you will spend an hour fixing something that was never wrong.

Six commands, in this order, place almost any fault:

```bash
# 1. Is the platform alive?
curl -s https://api.example.com/health/

# 2. Is the router reachable at all?
ssh deploy@SERVER_IP 'ping -c 3 10.10.0.N'

# 3. Did the request even arrive?
ssh deploy@SERVER_IP 'cd billing/backend && docker compose logs web --since 10m | grep hotspot'

# 4. What did it answer, and why?
ssh deploy@SERVER_IP 'cd billing/backend && docker compose logs web --since 10m | grep -A 30 Traceback'
```

```
# 5. What does the router think happened?
/log print where topics~"hotspot"

# 6. Is the portal's traffic being permitted?
/ip/hotspot/walled-garden/print
```

Steps 3 and 5 are the ones people skip, and they are the two that answer
"whose fault is it".

**Read the server, not the symptom.** Nothing today was diagnosed from what the
phone said.

---

### The portal never appears

Phone connects, no login page, no "sign in to network".

| Check | Command | Meaning |
|---|---|---|
| Hotspot server valid | `/ip/hotspot/print` | An `I` flag means *inactivated, not allowed by device-mode* — see §9.1. Everything downstream silently does nothing |
| Files present | `/file/print where name~"hotspot/"` | Files at the root instead of inside `hotspot/` are never served |
| Profile points at them | `/ip/hotspot/profile/print` | Wants `html-directory=hotspot` |
| Device bypassed | `/ip/hotspot/ip-binding/print` | A `bypassed` binding never sees the portal — easy to forget you added one for your own laptop |

Also: **try an `http://` address, not `https://`.** A captive portal cannot
intercept HTTPS without a certificate error, so typing `google.com` usually
just fails instead of redirecting. Phones probe over plain HTTP for exactly
this reason.

---

### Portal appears, no packages / "No connection to the payment service"

The page is served from the router, so it loading proves nothing about your API.

```
/ip/hotspot/walled-garden/print
```

- **`hits: 0`** — nothing matched. The request never got out. Add the address
  rule from §9.4; the hostname rule alone breaks after a reboot or against a
  phone using private DNS.
- **`hits` climbing** — traffic is getting through, so the fault is beyond the
  router. Go to the server logs.

Then confirm the request actually arrived:

```bash
docker compose logs web --since 10m | grep hotspot/packages
```

- **Nothing** — still the walled garden, or `API_BASE` in `config.js` is wrong
- **`200`** — the API answered; the problem is in the page, not the network
- **`400`** — usually the tenant token

Check `config.js` on the router really has the right values. It is the one file
edited per operator and the easiest to upload from the wrong folder.

---

### "Invalid or expired voucher"

Look at the voucher before believing the message:

```sql
select v.code, v.is_active, v.expires_at, s.status, s.expiry_date, now()
from billing_voucher v join billing_subscription s on s.id = v.subscription_id
order by v.id desc limit 5;
```

| Finding | Cause |
|---|---|
| `expires_at` in the past | Genuinely expired. Check the package's `duration_value`/`duration_unit` — a package named "1hr" configured as 5 minutes is a real thing |
| `is_active = f` | Already used or deactivated |
| Row looks fine | The code string did not match. Case is handled now; check for a typo, and that the operator's token in `config.js` matches the operator who issued it |

A voucher issued by one operator will never validate on another's portal. That
is deliberate — the lookup is scoped by tenant, and without it one operator's
code would grant access through another's hotspot.

---

### Router says "invalid username or password" after a valid code

The API succeeded and the router refused the login. Two different things.

```
/log print where topics~"hotspot"
```

Look for `trying to log in by http-chap` followed by a refusal.

| Check | Where |
|---|---|
| Was the user created? | `/ip/hotspot/user/print` — should show the device MAC with an `AUTO \| WIFI BILLING SYSTEM` comment |
| Does the profile exist? | `/ip/hotspot/user/profile/print` — `HOTSPOT_PKG_<id>_D<devices>` |
| Is CHAP the method? | `/ip/hotspot/profile/print` — `login-by` |
| Are the portal files current? | `/file/print where name~"hotspot/"` — `login.html` ≈33 KB and `md5.js` ≈8.8 KB are the fixed versions; ~4 KB and 7 KB are MikroTik's |

**`reset-html` overwrites your pages with MikroTik's.** After running it, always
re-upload the seven — otherwise the router is serving a portal that knows
nothing about your API.

To isolate the CHAP maths from everything else, temporarily:

```
/ip/hotspot/profile/set hsprof1 login-by=http-pap
```

If it logs in under PAP, the fault is in the hashing. Put it back afterwards.

---

### Customer still online after expiry

First, separate two things. **"Connected" on the phone is the WiFi association
and never goes away** — no captive portal drops it. What should stop is traffic.

```
/ip/hotspot/active/print
/ip/hotspot/host/print
```

- **No active session, host `authorized: False`** — working correctly. The
  customer keeps a WiFi icon and no internet, and gets the portal on their next
  HTTP request.
- **Session still present** — the disable never ran, or never reached the
  router.

```bash
docker compose logs worker --since 30m | grep -iE "expiry|disable"
```

You want `[expiry] Subscription N expired` followed by `[disable_customer_task]
Access disabled`. If the first appears and the second does not, the router was
unreachable — the database and the hardware have diverged, and the customer is
online while the dashboard says expired.

The sweep runs every 5 minutes, so up to five minutes of overrun is normal.
`limit-uptime` on the hotspot user is what makes it prompt; the sweep is the
backstop.

---

### One voucher put the whole site online

Or: usage figures are near zero for everybody, or the platform sees one
customer where there are twenty. All the same fault, on a site using an
external access point (§9.2b).

```
/ip/hotspot/active/print
/ip/hotspot/host/print
/ip/dhcp-server/lease/print
```

**One active session and one lease, while several people are connected**, means
the AP is routing rather than bridging. Every wireless client is NATed behind
the AP's single address and MAC before it reaches the MikroTik, so the hotspot
is authenticating the AP, not the customers.

Nothing on the router is wrong and nothing needs changing there. Fix it on the
AP: mode Bridge / Access Point, its DHCP server off, uplink in a LAN port
rather than the WAN port. Then re-check that two phones produce two leases.

The same shape explains near-zero usage across the board: `collect_hotspot_usage`
is reading one session's counters and attributing them to one customer, while
everyone else has no session to read.

---

### Router shows offline in the dashboard

```bash
docker compose logs worker --since 15m | grep router-health
ssh deploy@SERVER_IP 'ping -c 3 10.10.0.N'
```

If the platform cannot ping it, the dashboard is right and the fault is at the
site. On the router:

```
/ip/address/print
/ping 8.8.8.8 count=3
/ping vpn.example.com count=2
/ping 10.10.0.1 count=3
```

Read them in that order — each rules out a layer:

| Symptom | Cause | Fix |
|---|---|---|
| No address on ether1 | No WAN. Cable, or upstream device down | `/interface/print where name=ether1` for the `R` flag; then `/ip/dhcp-client/renew` |
| `8.8.8.8` fails | Router has an address but no route | Check the upstream device |
| `vpn.example.com` fails | DNS | `/ip/dns/print` |
| Server pings, `10.10.0.1` does not | **Tunnel needs re-resolving** | See below |

**A router that boots while its internet is down does not rejoin the tunnel by
itself.** WireGuard resolves the endpoint at startup, fails, and does not retry
aggressively. This is the common one after a power cut, because the MikroTik
boots faster than the modem in front of it:

```
/interface/wireguard/disable wg-smartbill
/interface/wireguard/enable wg-smartbill
/ping 10.10.0.1 count=3
```

Worth automating on every router — see [Self-healing the
tunnel](#self-healing-the-tunnel).

---

### "Test connection" fails on a router you believe is fine

Work outwards:

```bash
ssh deploy@SERVER_IP 'ping -c 3 10.10.0.N'                                    # tunnel
ssh deploy@SERVER_IP 'timeout 5 bash -c "</dev/tcp/10.10.0.N/8728"'            # port
```

- **Ping works, port does not** — the firewall rule is missing or below a drop.
  ICMP has its own accept rule in the default config, which is why ping can
  succeed while 8728 is silently dropped. This reads exactly like a wrong
  password.
- **Both work** — it is the credentials.

```
/ip/service/print where name=api
/user/print detail where name=billing
/ip/firewall/filter/print where comment~"Billing"
```

All three should be restricted to `10.10.0.1/32`.

---

### Works plugged direct, dead through the router — but WinBox is fine

The laptop gets online with the cable straight into it, and nothing at all when
the same cable goes into ether1 with the laptop on ether2. WinBox connects
throughout, which is what makes this one take an hour.

**WinBox proves almost nothing here.** It rides Layer 2 by MAC address, so it
works when the laptop has no IP, when the router has no upstream, and when
every forwarded packet is being dropped. All it demonstrates is that the cable
and the switch chip are alive. Look at its *Connect To* field: a MAC rather
than an address means you never had IP connectivity to prove.

Ask the router what it thinks it is:

```
/ip/address/print
/ip/dhcp-client/print
/interface/bridge/port/print
/ip/firewall/nat/print
```

**The board is a transparent bridge, not a router**, if you see ether1 listed
as a bridge port, the DHCP client sitting on the *bridge* rather than on
ether1, and an empty NAT table. It took an address from your upstream as an
ordinary client — which is why the router itself pings 8.8.8.8 perfectly while
nothing behind it works. There is no "behind it". Anything on ether2 is on the
upstream's segment, competing for a second lease it may not get.

Convert it (§9 assumes a routed board throughout), and **order the commands so
the address you are connected on dies last**:

```
/ip/address/add address=192.168.88.1/24 interface=bridgeLocal
/ip/pool/add name=dhcp-pool ranges=192.168.88.10-192.168.88.254
/ip/dhcp-server/add name=dhcp-lan interface=bridgeLocal address-pool=dhcp-pool disabled=no
/ip/dhcp-server/network/add address=192.168.88.0/24 gateway=192.168.88.1 dns-server=8.8.8.8,1.1.1.1
/ip/dns/set allow-remote-requests=yes

/interface/list/add name=WAN
/interface/list/add name=LAN
/interface/list/member/add list=WAN interface=ether1
/interface/list/member/add list=LAN interface=bridgeLocal

/ip/firewall/nat/add chain=srcnat action=masquerade out-interface-list=WAN

/interface/bridge/port/remove [find interface=ether1]
/ip/dhcp-client/add interface=ether1 use-peer-dns=yes add-default-route=yes disabled=no

/ip/dhcp-client/remove [find interface=bridgeLocal]
```

Everything except the last line is safe from a session on the old address — the
bridge carries both addresses at once. The last line drops you; release and
renew on the laptop and come back on `192.168.88.1`. Pick a LAN subnet that
differs from the upstream's: `192.168.88.0/24` behind a `192.168.8.0/24`
uplink, which look alike and are not.

`allow-remote-requests=yes` is needed before the hotspot works at all — the
walled garden permits addresses by watching DNS answers, and it can only watch
lookups that come to the router.

**Partial packet loss afterwards means you left the old DHCP client running.**
Two clients that present the same MAC — a bridge inherits the MAC of a member
port — get handed the same lease, and you end up with the address bound on two
interfaces and two default routes flagged `+` for ECMP. The router then
alternates between a working path and a dead one:

```
  0 8.8.8.8      timeout
  1 192.168.8.17 host unreachable
  2 8.8.8.8      41ms
```

That is not a flaky link. It is `/ip/route/print` showing two of everything.

---

### Whole LAN says "connected, no internet"

Check the router's own WAN before anything else:

```
/ip/address/print
/ping 8.8.8.8 count=3
```

If the router has no internet, every device behind it is correct to say so, and
the hotspot is irrelevant. This is worth ruling out first every single time — it
costs ten seconds and it is a common cause.

If the router is online and only *one* segment is not, and you have split the
LAN (§9.7), the usual culprit is the new bridge missing from the `LAN`
interface list — its DHCP and DNS are then dropped by the router's own
firewall.

```
/interface/list/member/print
```

---

### Dashboard login fails / data does not load

Open the browser console first. `GUIDE.md` is right about this: CORS failures
look like nothing, and the server logs a healthy `200`.

```bash
curl -s -o /dev/null -D - -X OPTIONS https://api.example.com/api/auth/login/ \
  -H "Origin: https://app.example.com" \
  -H "Access-Control-Request-Method: POST" | grep -i "access-control-allow-origin"
```

- **Header present** — CORS is fine, look elsewhere
- **Header absent** — that origin is not permitted. Browsing from a
  `*.vercel.app` preview URL rather than your real domain does this, and the
  frontend reports it as *invalid username or password*

---

### 500 on any endpoint

The traceback is logged now:

```bash
docker compose logs web --since 10m | grep -A 30 "Traceback"
```

If you get nothing, the `LOGGING` block in `settings.py` is missing or the
container is running older code. Check `docker compose ps` for uptime and pull.

---

### Tasks not running

```bash
docker compose logs beat --since 20m | grep Scheduler
docker compose logs worker --since 20m | tail -20
docker compose ps
```

**Beat dying is the silent failure in this stack** — no expiry sweeps, no
reminders, no router health, no failover, and nothing anywhere reports an
error. If beat is scheduling but the worker never receives, the broker is the
problem: check `REDIS_URL` has no database number on the end.

---

### Useful RouterOS commands

```
/log print where topics~"hotspot"      # portal and login attempts
/log print where topics~"wireguard"    # tunnel
/log print where topics~"dhcp"         # address problems
/ip/hotspot/active/print               # who is online now
/ip/hotspot/host/print                 # who is connected, authorised or not
/ip/hotspot/user/print                 # accounts the platform created
/interface/print                       # R flag = link up
/system/resource/print                 # uptime, memory, version
/export file=backup-name               # full config, downloadable from Files
```

`/export` is the one to run **before** touching anything on a live router. It
writes every command needed to rebuild the configuration, so a mistake becomes
an import rather than a site visit.

---

### Self-healing the tunnel

Every operator's router will lose power, and many come back before the modem in
front of them. Add this to each router so you are not driving out to run two
commands:

```
/system/scheduler/add name=wg-watchdog interval=2m on-event={
  :if ([/ping 10.10.0.1 count=2] = 0) do={
    /interface/wireguard/disable wg-smartbill;
    :delay 2s;
    /interface/wireguard/enable wg-smartbill;
    :log warning "wg-watchdog: tunnel down, re-resolved endpoint";
  }
}
```

Two failed pings, then re-resolve the endpoint. It logs when it acts, so
`/log print where topics~"script"` tells you whether a site is flapping.

**`scheduler` is disabled by `device-mode` on a `home` router**, as is `proxy`.
`hotspot` is not — a `home` board already permits it. Check rather than assume:

```
/system/device-mode/print
/system/device-mode/update hotspot=yes proxy=yes scheduler=yes
```

then power-cycle to confirm. Name every flag you want in one `update` and do it
at the same time as §9.1, so a single trip to the site covers all of them.

---

## Bugs found during this build

Each was silent, and each was found only by reading the server rather than the
symptom. Worth knowing they existed, because the shape repeats.

| Bug | What it looked like |
|---|---|
| No `LOGGING` config | Every 500 in production discarded — no traceback, ever |
| Voucher codes case-sensitive | Android capitalises the first letter; customers told a code they had just paid for was invalid |
| `comment` sent to `/ip/hotspot/user/profile` | RouterOS rejects the whole request; **every** first activation failed |
| `remove(**{".id": ...})` in ten places | Repeat activations failed — and **nobody was ever disconnected at expiry** |
| `web` published on all interfaces | Django reachable from the internet past ufw and past TLS |
| Guide's `.env` block | Four errors, each failing at a different stage |
| Backup dumped as the app role | RLS filtered the dump — schema, almost no rows, plausible size |
| `MD5()` UTF-8 encoded the CHAP challenge | Every voucher rejected as *invalid username or password*. The challenge is 16 raw bytes; UTF-8 expands anything above `0x7F` into two, so the hash was taken over the wrong data. Odds of 16 random bytes all landing under `0x7F` are about 1 in 65,000, so it failed essentially always — and it looked like a billing fault, not a JavaScript one |
| CAP mode left on a redeployed router | The SSID simply did not exist. `wlan1` disabled, `mode=station`, owned by a CAPsMAN controller that was not on the network — so the radio stayed down and every local setting was discarded without a word |
| Router shipped as a transparent bridge | ether1 in the bridge, DHCP client on the bridge, no NAT. Worked plugged direct, dead through the router, WinBox fine throughout — because WinBox rides Layer 2 and never needed an IP |
| `defconf: fasttrack` on a hotspot router | Nothing, by design. Customers online, portal fine, vouchers redeeming, byte counters frozen — every subscriber reading as having used almost nothing, discoverable only from a usage report weeks later. Caught by reading the default rule set rather than by any symptom, and it came **back** on a factory reset after having been removed once |
| Two operators issued the same M-Pesa till, transposed | The store number and till number swapped between them: one operator worked, the other's STK push was rejected by Safaricom with no PIN prompt on the customer's phone, nothing in the dashboard and nothing in the logs. The credentials test passes either way, because OAuth is fine and only the password signature is wrong. The settings page showed neither number's role, which is what made it unreadable |
