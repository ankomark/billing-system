# From nothing to a customer online

The build order, as actually performed, with the things that went wrong kept in.
`GUIDE.md` explains *why* each piece exists; this is the sequence and the
commands.

Placeholders: `example.com` is your domain, `SERVER_IP` your Hetzner address,
`TENANT_TOKEN` an operator's token. Real values live in your password manager,
not here — this file is in a public repository.

**Parts 1–6 you do once.** **Parts 7–11 you repeat for every operator.**

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

**On each operator's MikroTik** (RouterOS 7+; check with `/system resource print`):

```
/interface/wireguard/add name=wg-smartbill listen-port=13231
/ip/address/add address=10.10.0.N/24 interface=wg-smartbill
/interface/wireguard/peers/add interface=wg-smartbill \
  public-key="SERVER_PUBLIC_KEY" \
  endpoint-address=vpn.example.com endpoint-port=51820 \
  allowed-address=10.10.0.1/32 persistent-keepalive=25s
/interface/wireguard/print
```

Take the `public-key` from that last command — **use `print`, not `print
detail`**, which also prints the private key.

`allowed-address=10.10.0.1/32`, not `0.0.0.0/0`. The tunnel carries management
traffic only; routing a hotspot's browsing through a 600 MHz MIPS CPU doing
software crypto, via Germany, is not what you want.

`persistent-keepalive=25s` is what makes CGNAT work at all — the NAT mapping
expires in seconds, and your server can only reach back through one the router
is holding open.

**Back on the server:**

```bash
sudo /home/deploy/wg-add-peer.sh OPERATOR ROUTER_PUBLIC_KEY 10.10.0.N
```

That applies the peer live rather than restarting the interface, which would
drop every other operator until their keepalive next fires.

**Prove both directions.** From the router `/ping 10.10.0.1`, and from the
server `ping 10.10.0.N`. The second is the one that matters — the router
dialling out doesn't prove your server can reach back.

---

## Part 9 — The MikroTik

### 9.1 Allow the hotspot feature

RouterOS 7.13+ disables hotspot until someone physically present says otherwise.

```
/system/device-mode/print
/system/device-mode/update hotspot=yes
```

**Then pull the power and leave it off for ten seconds.** A software reboot does
not count. You have about five minutes. `/ip/hotspot/print` will show the
server flagged `I` — *inactivated, not allowed by device-mode* — until this is
done, and everything downstream silently does nothing.

Enable only `hotspot`, not `mode=enterprise`, which turns on container,
scheduler, socks, fetch and proxy as well.

### 9.2 Create the hotspot

```
/ip/hotspot/setup
```

| Prompt | Answer |
|---|---|
| hotspot interface | `bridge` |
| local address / masquerade / pool | accept defaults |
| select certificate | `none` |
| smtp server | accept |
| dns servers | `8.8.8.8,1.1.1.1` |
| **dns name** | **`login.hotspot`** |
| local hotspot user | `admin` + a real password |

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

### 9.3 Walled garden — the one that breaks portals

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

### 9.4 API access, over the tunnel

```
/ip/service/set api disabled=no port=8728 address=10.10.0.1/32
/user/add name=billing password=STRONG group=full address=10.10.0.1/32 comment="SmartBill"
/ip/firewall/filter/add chain=input action=accept protocol=tcp dst-port=8728 \
  src-address=10.10.0.1 in-interface=wg-smartbill comment="Billing API via tunnel" place-before=0
```

The firewall rule is required: RouterOS accepts input from its LAN interface
list and drops the rest, and `wg-smartbill` isn't in it. Ping works (ICMP has
its own rule) while TCP 8728 is silently dropped — which reads as a wrong
password.

Restricting both the service and the user to `10.10.0.1/32` is stronger than
restricting to a public IP: a tunnel address is reachable only by something
already holding your server's private key, and it doesn't change when you
move hosts.

If the user already exists, `/user/add` fails — use `/user/set` instead.

### 9.5 Register it

Operator dashboard → **Routers → Add router**: address `10.10.0.N`, username
`billing`, port `8728`. **Press Test connection before saving.** It tells you
which of the address, the firewall, or the password is wrong while you are
still standing in front of the machine.

**Do not create user profiles by hand.** The backend builds and maintains
`HOTSPOT_PKG_<id>_D<devices>` and `PPPOE_PKG_<id>` from the package definition,
so a manual edit is overwritten the next time somebody buys.

### 9.6 Optional — free ports alongside the paid hotspot

For an operator who wants their own desks, a till or an office PC online
without paying: give those ports a separate bridge. The hotspot captures
whatever interface it runs on, so the fix is to take ports *out* of `bridge`
rather than to configure the hotspot.

This example frees **ether3** and **ether4** and leaves ether2, ether5 and the
WiFi behind the portal.

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

Then point the profile at them:

```
/ip/hotspot/profile/set hsprof1 login-by=http-chap,http-pap
```

> **Currently unresolved — see Open items.** The portal reads `$(chap-id)` and
> `$(chap-challenge)` from HTML attributes, and RouterOS emits those as
> JavaScript escape sequences, so the hash is computed over the wrong bytes and
> the router answers *invalid username or password*. Until that is fixed, use
> `login-by=http-pap`.

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

1. Operator dashboard → **Settings**: consumer key, consumer secret, shortcode,
   passkey
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

## Open items

- **CHAP login — fixed, not yet proven.** Both faults are corrected and the
  files are on the router: the challenge is now substituted into a JS string
  literal, and `MD5()` no longer UTF-8 encodes its input. Not yet confirmed
  with `login-by=http-chap,http-pap` on real hardware. **Test it three or four
  times, not once** — the UTF-8 fault only corrupted challenges containing a
  byte above `0x7F`, so roughly half of attempts succeeded by luck even before
  the fix, and a single success proves nothing.
- **Off-site backups.** Local only until `rclone` has a remote — the dumps
  currently share a disk with the database.
- **`PermitRootLogin no`** once no further root work is expected.
- **Android MAC randomisation.** Device binding is by MAC. A phone that rotates
  its address looks like a new device and can exhaust a one-device package.
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
  rule from §9.3; the hostname rule alone breaks after a reboot or against a
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
LAN (§9.6), the usual culprit is the new bridge missing from the `LAN`
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

**`scheduler` is disabled by `device-mode` on a `home` router**, the same as
hotspot was:

```
/system/device-mode/update scheduler=yes
```

then power-cycle to confirm. Do it at the same time as the hotspot change so
one trip covers both.

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
