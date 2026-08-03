# Deployment guide

Backend on a Hetzner box, frontend on Vercel, portal files on each operator's
MikroTik.

Read the whole of a section before starting it. The three parts depend on each
other — the frontend needs the backend's hostname, and the routers need both —
so the order below is the order to do them in.

---

## Before anything

Two decisions and one question. All three are cheaper to settle now than to
discover later.

**A domain name.** You need one, and you need it before you configure anything,
because both the M-Pesa callback and every operator's router will point at it.
Two subdomains:

| Name | Points at | Used by |
|---|---|---|
| `api.yourdomain.com` | the Hetzner box | the portal, the dashboard, Safaricom |
| `app.yourdomain.com` | Vercel | operators signing in |

**Never give anyone a bare IP address.** Not Safaricom, not an operator
configuring their router, not a firewall rule. The day you move hosts, a
hostname is a DNS change and an IP is twenty phone calls.

**Ask your operators one question before you promise them anything:**

> On your MikroTik, run `/ip address print` and look at the address on your
> internet-facing interface. Then from a phone on that same connection, open a
> "what's my IP" site. Do the two match?

| Answer | Meaning | What it costs you |
|---|---|---|
| They match, and it doesn't change | Public static IP | Nothing. Proceed as written. |
| They match but changes | Public dynamic | Use MikroTik's free DDNS name (below) |
| They differ, or the router's address starts `100.64`–`100.127` | **CGNAT** | Your server cannot reach them. See [CGNAT](#when-a-router-cannot-be-reached). |

Mobile and LTE connections are essentially always CGNAT. Starlink residential
is CGNAT by default. If your operators are on those, read the CGNAT section
before you buy a server, because it changes what you build.

---

## Part 1 — The backend, on Hetzner

### 1.1 The server

A CPX21 (3 vCPU, 4 GB) comfortably runs all six containers. Take Ubuntu LTS.

Hetzner verifies identity at signup and it is not always quick for customers
outside Europe — **open the account before you plan around it.**

Add your SSH key during creation rather than using a root password.

### 1.2 Lock it down first

Before Docker, not after.

```bash
ssh root@your-server-ip

adduser deploy && usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy

ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw enable
```

Then turn off password logins — in `/etc/ssh/sshd_config` set
`PasswordAuthentication no` and `PermitRootLogin no`, and
`systemctl restart ssh`. Keep your current session open until you have proved a
new one works.

> **Docker does not respect ufw.** It writes its own iptables rules ahead of
> yours, so a container that publishes a port is reachable from the internet
> even when ufw says otherwise. This repo's compose file binds Postgres and
> Redis to `127.0.0.1` for that reason. If you ever add a published port,
> bind it to loopback unless you genuinely mean the world to reach it.

Also switch on Hetzner's own Cloud Firewall in their console, allowing only 22,
80 and 443. It sits outside the machine, so it holds even if something on the
box is misconfigured.

### 1.3 Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker deploy
```

Log out and back in for the group to take effect.

### 1.4 The code and its secrets

```bash
git clone <your-repo> billing
cd billing/backend
```

Generate two keys. **These are not interchangeable and losing either is
expensive.**

```bash
# Signs sessions, password-reset links, and both hotspot tokens
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# Encrypts router passwords at rest
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Write `backend/.env`:

```ini
DEBUG=False
ENVIRONMENT=production
SECRET_KEY=<the first one>
FIELD_ENCRYPTION_KEY=<the second one>

ALLOWED_HOSTS=api.yourdomain.com
CORS_ALLOWED_ORIGINS=https://app.yourdomain.com
PLATFORM_BASE_URL=https://app.yourdomain.com

POSTGRES_DB=billing
POSTGRES_USER=billing
POSTGRES_PASSWORD=<a long random string>
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

REDIS_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

MPESA_ENV=production
MPESA_SHORTCODE=<yours>
MPESA_ALLOW_LOCAL_CALLBACK=False

# Optional
SENTRY_DSN=
AT_API_KEY=
AT_USERNAME=
```

Two things about this file:

**`SECRET_KEY` is mandatory.** With `DEBUG=False` the app refuses to start
without it, on purpose. The old fallback value is published in this repository,
and both hotspot tokens are HMACs over this key — deploying on the placeholder
would make them forgeable by anyone who read the source, silently, with every
test passing.

**`FIELD_ENCRYPTION_KEY` decides whether router passwords are encrypted.**
Leave it unset and they are stored in plain text. Nothing warns you. Every
operator's router admin password would then sit readable in a database you are
about to copy offsite every night.

Lock the file: `chmod 600 .env`.

### 1.5 Start it

```bash
docker compose up -d --wait
docker compose exec web python manage.py createsuperuser
```

Check all six are healthy with `docker compose ps`.

### 1.6 TLS

Your app listens on port 8000 and speaks plain HTTP. Something has to terminate
TLS in front of it. Caddy is the least work:

```bash
sudo apt install caddy
```

`/etc/caddy/Caddyfile`:

```
api.yourdomain.com {
    reverse_proxy localhost:8000
}
```

`sudo systemctl reload caddy`. Certificates are obtained and renewed on their
own.

Point `api.yourdomain.com` at the server's IP first, or the certificate request
will fail.

**Cloudflare in front is worth it**, and not only for DDoS. Cloudflare has a
Nairobi presence, so the TLS handshake finishes locally rather than in Germany
— which is most of what makes a distant server feel slow on first load. Use
Full (strict) mode so Cloudflare still verifies Caddy's certificate.

### 1.7 Backups — do this on day one

**A database dump alone will not restore this system.** Router passwords are
encrypted with `FIELD_ENCRYPTION_KEY`. Restore without it and every one of them
is an unreadable blob: the platform comes up perfectly and cannot reach a
single operator's router.

So back up two things, in two places.

**The key**, once, by hand, into a password manager. It never changes. Do not
put it in the same bucket as the dumps — a backup and its key in one place is
one break-in, not two.

**The database**, nightly. Create a bucket on Cloudflare R2 or Backblaze B2 —
both are cheap and neither charges much to get data *out*, which is what
matters on the day you need it. Then `/home/deploy/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail

STAMP=$(date +%Y%m%d-%H%M)
OUT=/tmp/billing-$STAMP.sql.gz

cd /home/deploy/billing/backend
docker compose exec -T postgres pg_dump -U billing billing | gzip > "$OUT"

# Encrypt before it leaves the machine — it holds customer names, phone
# numbers and payment records.
gpg --batch --yes --passphrase-file /home/deploy/.backup-pass \
    --symmetric --cipher-algo AES256 "$OUT"

rclone copy "$OUT.gpg" remote:billing-backups/
rm -f "$OUT" "$OUT.gpg"

# Keep 30 days
rclone delete --min-age 30d remote:billing-backups/
```

`chmod 700 backup.sh`, then `crontab -e`:

```
15 2 * * * /home/deploy/backup.sh >> /home/deploy/backup.log 2>&1
```

**Then restore one.** Into a scratch container, this week, before you have
customers. An untested backup is a belief, not a backup — and the failure you
are testing for is the one where the dump has been silently empty for a month.

---

## Part 2 — The frontend, on Vercel

The dashboard is a Create React App build. Static files, no server.

1. Import the repository in Vercel.
2. **Root directory:** `frontend/wifi-billing-frontend`
3. Framework preset: Create React App. Build `npm run build`, output `build`.
4. Environment variable:

   ```
   REACT_APP_API_URL = https://api.yourdomain.com/api/
   ```

   Set it for Production, Preview and Development, or preview deploys will
   quietly call nothing.
5. Add `app.yourdomain.com` under the project's Domains.

Then go back and make sure `CORS_ALLOWED_ORIGINS` in `backend/.env` contains
exactly `https://app.yourdomain.com` — no trailing slash, and `https` not
`http`. Restart the backend after changing it.

> Vercel preview deployments get generated hostnames like
> `yourapp-git-branch.vercel.app`, and each is a different origin the backend
> will refuse. If you want previews to work against production, add a pattern
> to `CORS_ALLOWED_ORIGIN_REGEXES` rather than listing them — they change every
> push.

**CORS failures look like nothing.** The browser refuses the response and your
server logs show a perfectly healthy 200. If the dashboard loads but no data
appears, open the browser console before you touch anything else.

---

## Part 3 — The MikroTik

Do this once per operator. Budget half an hour for the first, ten minutes after
that.

### 3.1 The one setting that everything depends on

**The captive portal must be able to reach your API before the customer has
logged in.** That is the entire point — the page has to fetch packages and
validate a code while the person is still blocked from the internet.

MikroTik blocks unauthenticated traffic by default, so you must let your API
through the walled garden:

```
/ip hotspot walled-garden
add dst-host=api.yourdomain.com comment="Billing API"
```

Miss this and the portal loads, shows no packages, and nothing an operator does
on the router will explain why. **It is the single most common failure when
setting one of these up.** If a portal looks broken, check this first.

If you put Cloudflare in front, allow the hostname as above rather than an IP —
the addresses behind it change.

### 3.2 Upload the files

Edit `mikrotik-hotspot/config.js` — **and only that file** — before uploading:

```js
var API_BASE     = 'https://api.yourdomain.com/api';
var TENANT_TOKEN = 'the operator's token';
```

The token comes from **Admin → Settings** in that operator's own dashboard. It
identifies whose portal this is. A device MAC is only unique within one
operator, so without it the backend cannot tell whose subscriber is connecting,
and refuses rather than guessing.

Nothing else in the folder is edited. The business name, packages, prices,
support numbers, terms and device limits all come from that operator's settings
— change them in the dashboard and these pages follow.

Then drag the whole folder into **Files** in WinBox, or:

```bash
scp -r mikrotik-hotspot/* admin@router-ip:/hotspot/
```

Files: `login.html`, `alogin.html`, `status.html`, `logout.html`, `config.js`,
`md5.js`, `smartbill.png`. All seven, or pages will half-work.

### 3.3 Point the hotspot at them

```
/ip hotspot profile
set [find name=hsprof1] html-directory=hotspot login-by=http-chap,http-pap
```

`http-chap` is why `md5.js` is in the folder — the page hashes the password
with the router's challenge rather than sending it in clear. `http-pap` stays
listed so a profile without CHAP still works.

### 3.4 The API user

Your server signs in to the router to create and remove customers. Give it its
own account, not the operator's:

```
/user add name=billing password=<long random> group=full comment="Billing platform"
/ip service set api disabled=no port=8728
```

Then in the dashboard, **Routers → Add**, with the router's address, that
username and password, and port 8728.

> **Restrict who may use that account.** On the router:
> ```
> /user set billing address=<your server's IP>
> ```
> If you move hosts this must be updated on every router — which is a good
> reason to have taken a static IP at Hetzner and to keep it.

### 3.5 What not to configure

The backend creates and maintains hotspot user profiles itself — rate limits,
shared-users for multi-device packages, session timeouts. They appear as
`HOTSPOT_PKG_<id>_D<devices>` for hotspot and `PPPOE_PKG_<id>` for PPPoE.

**Do not edit those by hand.** They are rebuilt from the package definition, so
a manual change is silently overwritten the next time somebody buys.

### 3.6 Check it works

From a phone, on that WiFi:

1. Connect. The portal appears.
2. **Packages are listed.** If not, it is the walled garden — go back to 3.1.
3. The operator's business name is at the top, not "WiFi".
4. Support numbers appear under the packages.
5. Buy the cheapest package. The M-Pesa prompt arrives, and after approving,
   the phone connects on its own.
6. Turn WiFi off and on. It reconnects without paying again.

If step 5 works but step 6 does not, the device binding did not save — check
the customer exists in the dashboard with a MAC against them.

### 3.7 Hotspot sharing (optional, off by default)

One subscriber buys a package, turns on their phone's own hotspot, and three
friends browse for free. Nothing else in the system can see it — the router
talks only to the phone, so it is one MAC and one session, and the device limit
you sold has nothing to say about what is behind that one phone.

What gives it away is the hop counter on the packets. A phone talking to your
router directly arrives with the round number its operating system set (64 on
Android and iOS, 128 on Windows); a laptop behind that phone has crossed one
more router, so it arrives one lower. The backend installs nine mangle rules
that write the odd ones into address lists, reads those lists every five
minutes, and builds a case per subscriber.

**It is evidence, not proof.** A subscriber who plugs your WiFi into their own
travel router looks identical, and anyone who has bothered to pin their TTL
back to 64 will never appear at all. So nothing happens on one sighting, and
nothing happens without you asking.

Switch it on per operator, in **SystemSetting**:

| Key | Values | Default |
|---|---|---|
| `TETHERING_POLICY` | `off`, `log`, `warn`, `throttle`, `kick` | `off` |
| `TETHERING_MIN_OBSERVATIONS` | sweeps before acting | `3` (≈15 min) |
| `TETHERING_THROTTLE_KBPS` | speed under `throttle` | `512` |
| `TETHERING_CONNECTION_LIMIT` | connections that mark an address busy | `100` |
| `TETHERING_STALE_MINUTES` | silence that closes a case | `30` |
| `TETHERING_MESSAGE` | what the subscriber is told | see below |

**Start on `log`, for a week.** It installs the rules and records what it finds
and does nothing else. Look at the cases in the admin before you let it act —
on some networks it turns up nothing, and on others it turns up a third of the
customer base, which usually means something about that estate, not about the
customers.

Then:

- `warn` texts them and leaves their access alone.
- `throttle` puts them on `TETHERING_THROTTLE_KBPS` until the sharing stops,
  and lifts it automatically when it does.
- `kick` ends the hotspot session, which is what puts them back at the login
  page — a firewall rule cannot do that, because an authenticated client's
  traffic is already being passed, so dropping packets gives them a broken
  connection rather than a login form.

None of them touches the subscription, blocks a device or burns a voucher. The
strongest setting interrupts a session; the customer can log straight back in.

**Two things it cannot see, both worth knowing before you trust it.**

*A pinned TTL.* Setting the outgoing hop counter back to 64 is one line on a
rooted Android, and there are apps that do it. Nobody who has bothered will
ever appear in the hop lists. What they cannot hide is how many connections a
roomful of devices holds open at once, so a tenth rule marks addresses above
`TETHERING_CONNECTION_LIMIT` as busy. That flag is corroboration only — one
enthusiastic torrent client trips it alone — so it is recorded on the case and
shown in the admin, and nothing is ever done on the strength of it.

*IPv6.* MikroTik's hotspot is IPv4 only: it does not intercept IPv6, does not
authenticate it, and these rules do not match it. On a router handing out
global IPv6, sharing over IPv6 is invisible here — and a device that never
logged in may not need to. Check with `/ipv6 address print`; anything not
starting `fe80` on a client-facing interface is the hole. The sweep logs a
warning when it sees one, and `tethering_rules status` prints it. If you are
not deliberately running IPv6, turn it off on the client bridge.

To take it all off a router again:

```bash
docker compose exec backend python manage.py tethering_rules remove --tenant <slug>
docker compose exec backend python manage.py tethering_rules status --tenant <slug>
```

The rules are also written out in `mikrotik-hotspot/tethering-detection.rsc` if
an operator would rather paste them in and watch the address lists themselves
before letting the backend act on anything.

---

## When a router cannot be reached

If an operator came back CGNAT, your server cannot dial their router, and no
hosting choice changes that. Their ISP has put them behind shared address
space, and only their ISP can take them out of it — usually by selling them a
static public IP, which is often the simplest answer and worth pricing before
building anything.

Otherwise, reverse the direction: the router dials out to you and holds the
connection open, and you address it through the tunnel. RouterOS 7 has
WireGuard built in.

On your server, one WireGuard interface with a peer per operator. On the
router, an interface pointing at **`vpn.yourdomain.com`, never an IP** —
RouterOS resolves the endpoint at handshake, so moving hosts becomes a DNS
change instead of reconfiguring every router you have.

Then in the dashboard, that router's address is its tunnel address
(`10.10.0.23`) rather than a public one. Nothing else changes — the platform
does not know or care which it is.

For a **public but dynamic** address, MikroTik gives you a free DNS name:

```
/ip cloud set ddns-enabled=yes
/ip cloud print
```

Use the `xxxxx.sn.mynetname.net` name in the dashboard instead of the address,
and it follows the router when the ISP changes it.

---

## Moving later

You are not locked in. To move to another provider: rent the box, install
Docker, clone the repo, copy `.env`, restore the dump, `docker compose up -d`,
repoint DNS.

The window is 15–60 minutes, and here is the part worth knowing: **subscribers
who are already connected stay online throughout.** The MikroTik enforces
access locally — the user and their time limit live on the router. What stops
is new purchases and new provisioning. A migration costs you an hour of sales,
not an outage for people who have already paid.

Two things make that easy, and both have to be done in advance:

- **Hostnames everywhere.** Never an IP in a router config, a firewall rule, or
  a Safaricom callback.
- **`/user set billing address=` on every router.** This is the one that will
  bite you, because it is per-router and needs changing before the new server
  can log in.

---

## When something is wrong

**Portal shows no packages** — walled garden (3.1). Almost always this.

**Dashboard loads, no data** — CORS. Open the browser console. Check
`CORS_ALLOWED_ORIGINS` matches the Vercel domain exactly, scheme and all.

**Router shows offline** — reachability. From the server:
`nc -zv <router-ip> 8728`. If that fails it is CGNAT, a firewall, or
`/user set billing address=` pointing somewhere else. A router is only declared
down after three failed probes in a row — about six minutes — so a brief drop
will not show here.

**Payment taken, customer not connected** — check **M-Pesa Payments** in the
dashboard for the transaction, then **Failed Connections**. If the payment
arrived and no connection attempt was recorded, the callback did not reach you:
confirm the URL registered with Safaricom matches `api.yourdomain.com` and that
`MPESA_ALLOW_LOCAL_CALLBACK=False`.

**Customers connect and disconnect repeatedly** — usually the package's device
limit being shared further than it was sold. **Failed Connections** shows
`device_limit` when that is what is happening.

Logs: `docker compose logs -f web`, or `worker` for anything scheduled.
