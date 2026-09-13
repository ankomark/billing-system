# Router runbook

What goes wrong on the MikroTiks, how to tell which one it is, and what to do
about it.

Every entry here is a fault that actually happened on this estate, with the
evidence that identified it and the fix that was applied. Written because the
hard part was never the fix — it was working out which of half a dozen very
different faults produces the same sentence from a customer: **"it says
connected but there is no internet."**

Start with the symptom. Each entry gives you a command that confirms or rules
it out in under a minute, so you can eliminate rather than guess.

The settings this describes are re-asserted every hour by
`billing.tasks.router_tasks.ensure_lease_script_task`. A router that is reset,
restored from an old backup, or swapped for a spare comes back without them and
nothing announces it — the symptom is simply that subscribers at that station
start complaining again.

---

## Quick triage

| Customer says | Most likely | Confirm with |
|---|---|---|
| Connected, no internet, **no login page** | [DoH bypass](#1-the-portal-never-appears-dns-over-https) | `/ip hotspot walled-garden print` |
| Connected, no internet, valid voucher | [Stranded session](#2-connected-no-internet-with-a-valid-voucher) | `/ip hotspot active print where idle-time>10m` |
| Cut off early, dashboard shows little usage | [Usage blindness](#4-the-cap-fires-at-the-wrong-time) | compare account bytes to `usage_since` |
| Cut off at a fraction of the bundle | [Allowance over-split](#5-cut-off-at-a-fraction-of-the-bundle) | `/ip hotspot user print` → `limit-bytes-total` |
| PPPoE: connected, HTTPS hangs, ping fine | [No MSS clamping](#6-pppoe-connected-but-nothing-loads) | `/ppp profile print` → `change-tcp-mss` |
| PPPoE: drops and redials all day | [Keepalive too tight](#7-pppoe-drops-and-redials) | `/interface pppoe-server server print` |
| Everyone at one station, after a power cut | [Mass stranding](#3-after-a-power-cut) | both of the above |

---

## 1. The portal never appears (DNS-over-HTTPS)

**Symptom.** Device joins the WiFi, gets an address, and shows "connected, no
internet". No sign-in page, no notification, nothing to tap. Affects people who
have never paid as much as people whose bundle ran out — and for the second
group the system has done everything right: suspended them, removed the
account, ended the session. They still cannot see where to buy another.

**Why.** A captive portal only works by being the only thing a device can
reach. The redirect depends on the router answering the device's DNS. If an
unauthorised device can resolve names *itself*, the operating system never
concludes it is behind a captive portal and never offers the sign-in prompt —
while every real connection is still dropped.

Found on both routers: walled-garden rules accepting `tcp/443` to `1.1.1.1`,
`1.0.0.1`, `8.8.8.8` and `8.8.4.4`. That is DoH to Cloudflare and Google,
allowed for devices that had not logged in. They carried an `AUTO | WIFI
BILLING SYSTEM` comment and nothing in the repository creates them, so they
outlived whatever version did.

**Confirm.**

```
/ip hotspot walled-garden print
/ip hotspot walled-garden ip print
```

Anything pointing at a public resolver on port 443 or 853 is the fault.

**Fix.** Remove those rules. Nothing else. The phone's DoH then fails, it falls
back to the DNS the router hands out, interception works, and the portal
appears. Authorised subscribers are unaffected — the walled garden governs only
devices that have not logged in, so a paying customer's DoH keeps working.

`billing/services/walled_garden.py` does this and is swept hourly. It is
deliberately narrow: only a known public resolver, and only on 443 and 853.
Plain 53 is never touched — that is the fallback the device needs, and the
hotspot answers it.

**Do not remove** the Billing API rules. The portal cannot load without them:

```
/ip hotspot walled-garden add dst-host=api.smartbillsolution.com action=allow
/ip hotspot walled-garden ip add dst-address=167.233.247.151 action=accept
```

---

## 2. Connected, no internet, with a valid voucher

**Symptom.** One device at a time. Holds a good voucher, is on the network, and
passes nothing. Usually resolves itself eventually, which is what makes it hard
to catch.

**Why.** A hotspot session is keyed by **MAC and address together**. When a
device's DHCP lease moves, the authorisation stays behind on the old address
and the device's traffic arrives from the new one, unauthorised.

Two shapes, and the second is the common one:

- **moved** — the device has a bound lease on a different address
- **recycled** — the address it is authorised on now belongs to a *different*
  handset. 59 sessions were in this state at once, every one with no lease of
  its own: the device went away, its 15-minute lease expired, the address was
  handed to somebody else, and nothing reaped the session.

Nothing on the router undoes this by itself. `keepalive-timeout` would, and it
is off deliberately — see [§8](#8-why-keepalive-is-off-on-the-hotspot).

**Confirm.**

```
/ip hotspot active print where idle-time>10m
/ip dhcp-server lease print where address=<the session's address>
```

If the lease on that address belongs to another MAC, or the session's MAC has a
lease elsewhere, that is it.

**Fix.** Remove the session **and its host entry**. The host is not tidiness:
RouterOS will not re-run MAC authentication while a stale one stands, so
removing only the session leaves the device exactly as stuck.

Handled at source by the DHCP lease script (§3) and swept every five minutes by
`billing/services/duplicate_sessions.py`.

---

## 3. After a power cut

**Symptom.** The routers come back and a large fraction of subscribers at that
station are connected with no internet, all at once.

**Why.** Every device re-leases on reboot, and any whose address changed is
stranded exactly as in §2 — but the whole estate at the same moment.

**The fix is a DHCP lease script**, not a lease-time change. RouterOS runs
`lease-script` on every bind and unbind, which is the exact moment an address
changes hands.

```
/ip dhcp-server set [find where interface=bridge] lease-script="..."
```

The script body lives in `billing/services/lease_script.py` and is installed
and re-asserted hourly. What it does:

- **unbind** — removes the departing MAC's session and host on the address it
  is giving up, so the address is clean before the next holder gets it. It
  matches MAC *and* address rather than address alone, so it does not depend on
  RouterOS firing before reassignment.
- **bind** — removes any session for that MAC on a *different* address, which
  is the stranded-after-reboot case.

A device removed this way is not cut off: it re-runs MAC authentication on the
host entry its next packet creates.

**Proven before it shipped:** 80 settled devices re-bound to the address they
already held → nothing removed. A genuinely stranded session → removed exactly.
Live result: stranded sessions went from 59 to 0, duplicated MACs 87 to 0, and
session counts went slightly *up* — nobody was logged out.

### Why `lease-time` must not be raised

This is counter-intuitive and worth stating plainly, because raising it is the
obvious move and it is wrong here.

- Pool `default-dhcp` is `192.168.88.10-254` — **245 addresses**
- skylink held **222 bound leases** against it — 90% full, 23 to spare

A longer lease multiplies how long a *departed* device keeps its address. At
90% occupancy that runs the pool dry, and a customer who cannot get an address
never reaches the portal at all — a worse failure than the one being fixed. The
15-minute lease is load-bearing.

The pool cannot simply grow either: widening the bridge subnet to /23 or /22
collides with `pppoe-pool` on 192.168.89.x and `free-internet-pool` on
192.168.90.x. Growing it means moving the hotspot to another subnet, which is a
migration, not a setting.

`store-leases-disk=5m` is already set and should stay — it is what lets leases
survive a reboot at all.

---

## 4. The cap fires at the wrong time

**Symptom.** A subscriber is cut off mid-bundle. The dashboard shows they have
used little or nothing. They are not marked suspended, get no "limit reached"
message, and typing their voucher does not explain anything.

**Why.** The router enforces `limit-bytes-total` itself. If our own usage
figure is lower than the router's, the router cuts them off before anything
here has decided to — so nothing tells them why.

The cause was that sessions were matched by `customer.hotspot_username` — one
MAC, fixed at the first device a subscriber ever used — while `mac-auth-mode`
is `mac-as-username` and names every session after the handset that opened it.
**85 of 347 entitled devices online (25%)** matched nothing and recorded zero.

**Confirm.** Compare the router's own meter against what we recorded:

```
/ip hotspot user print where name=<MAC>
```

`bytes-in + bytes-out` against `usage_since(customer, window_start(sub))`. A
large gap in the router's favour is this fault. Observed: one subscriber
metered 3420 MB against 308 recorded, another 1100 against 71, a dozen showing
hundreds of MB against zero.

**Fix.** Fixed in `billing/tasks/usage_tasks.py`: sessions are read by MAC
(`get_hotspot_sessions_by_mac`) and attributed through the device registry, and
the baseline is kept per **account** — device *and* station — because that is
what RouterOS meters.

Two rules that matter if this is ever touched again:

- **The first sight of an account records nothing.** There is no delta without
  a baseline, and treating the first reading as one charges a subscriber their
  whole session counter in a single interval.
- **A counter that went backwards is a reboot, not usage.** Re-baseline and
  claim nothing rather than inventing a delta the size of the counter.

### Usage must sum across stations

A subscriber has an account at every station. 1 GB used at one and 2 GB at
another is 3 GB against one bundle. `tenant_sessions` keeps only the *first*
router a username appears on — correct for PPPoE, where a session is exclusive,
and wrong for a hotspot. Use `tenant_sessions_everywhere`.

The quiet half of that fault: with one baseline per subscriber, the second
station looked like a counter going backwards, so it re-baselined and threw the
interval away — every poll, indefinitely.

---

## 5. Cut off at a fraction of the bundle

**Symptom.** A subscriber on a 3 GB package is stopped at 1 GB. A 40 GB
subscriber stopped at 13 GB.

**Why.** `limit-bytes-total` is counted **per hotspot user**, and a hotspot
user is one MAC. To enforce a shared cap the remaining allowance is divided by
the number of devices granted. When a handset rotates its MAC, a one-device
package accumulates two or three bound rows, so each gets a third of the
allowance and the rest is stranded on addresses that will never come back.

38 subscribers were split more ways than they had bought; two held three
devices against a one-device package and were given 13333 MB each of 40 GB.

**Confirm.**

```
/ip hotspot user print where name=<MAC>
```

If `limit-bytes-total` is a clean fraction of the package cap, count the
`CustomerDevice` rows bound to their current subscription.

**Fix.** `macs_to_grant` now trims to `package.max_devices`, most-recently-seen
first — so what gets dropped is the address a rotating handset left behind,
never the phone in use.

The invariant to preserve: **the divisor in `_remaining_data_bytes` and the
accounts written by `_grant_hotspot` must be the same set.** Divide by more
than is granted and the subscriber is short-changed; by fewer and every device
gets the whole allowance. `_trim_to_package_limit` exists to keep them one set.

---

## 6. PPPoE: connected but nothing loads

**Symptom.** The PPPoE session establishes. Ping works, DNS works, small pages
load — and every HTTPS site and every download hangs forever.

**Why.** PPPoE costs 8 bytes of header, so the usable MTU is **1492**, not
1500. A client negotiating the ordinary 1460-byte MSS sends segments the tunnel
cannot carry, and the ICMP that would say so is dropped on nearly every path.
Nothing reports an error.

RouterOS's own `default` and `default-encryption` profiles ship with clamping
on. Package profiles created by this system did not set it at all — so every
`PPPOE_PKG_*` profile was born without it, sitting beside correct built-in
ones.

**It also explains repeated redialling.** A CPE router that watches for a
working path finds none and dials again. One subscriber logged 13 session
restarts over four days, **most carrying zero bytes** — session up, passes
nothing, dies.

**Confirm.**

```
/ppp profile print
```

Any profile with `change-tcp-mss` not set to `yes` is the fault.

**Fix.**

```
/ppp profile set [find where name~"PPPOE_PKG"] change-tcp-mss=yes
```

`ensure_pppoe_profile` now sets it, and because that dict is what the repair
path compares against, profiles already on the routers are corrected on the
next provisioning run.

---

## 7. PPPoE: drops and redials

**Symptom.** The line drops and comes back repeatedly through the day.

**Why.** The PPPoE server's `keepalive-timeout` ships at **10 seconds**. On a
wireless backhaul an ordinary few-second interruption ends the session and the
subscriber's router redials.

**Confirm.**

```
/interface pppoe-server server print
```

**Fix.** Raise it to 60.

```
/interface pppoe-server server set [find] keepalive-timeout=60
```

The cost the other way is small: a session that has genuinely died lingers up
to a minute, and `only-one=yes` on every package profile means the redial
replaces it rather than queueing behind it. Asserted hourly.

Rule out §6 first — the MSS fault produces the same symptom and is the more
likely root.

---

## 8. Why keepalive is off on the hotspot

Every hotspot user profile carries `keepalive-timeout=none`, and `idle-timeout`
is not set either. Both are deliberate and should stay that way.

`none` here is written **explicitly**, not left unset — RouterOS defaults this
to 2m, and the profile overrides it. If you see a profile without it, that
profile is wrong.

A sleeping phone fails a keepalive exactly like a departed one: over ARP the
two are identical. The numbers, measured on this estate:

- at 2m, subscribers were reaped constantly
- raising it to 5m only moved the threshold — skylink still logged **44
  keepalive logouts in 47 minutes**, skylink3 **81**, against 214 logins in the
  same window on a site with roughly 60 real sessions

Everyone was being reaped and silently re-admitted by their cookie several
times an hour, which is exactly the "disconnected and reconnected after a few
minutes" customers report. **There is no correct number.** Any threshold short
enough to reap a departed device is short enough to reap a sleeping one.

`idle-timeout` is worse again: it measures traffic rather than reachability, so
it disconnects somebody who is connected and simply not using it.

What ends a session instead, now that ARP does not:

- `limit-uptime`, written at grant time as the subscriber's remaining
  wall-clock (§10)
- `disable_customer_access` at expiry, which removes the user and drops the
  session in the same call
- the subscriber logging out, or the operator disconnecting them
- the lease script (§3), which acts on an address changing hands rather than on
  a device being quiet

None of those mistakes a locked phone for a departed one.

**The cost**, stated honestly: a device that leaves without logging out keeps
its session until one of the above fires, holding a `shared-users` slot
meanwhile. On these packages `shared-users` is 2, so a household with one
device away still has a free slot, and the slot returns at expiry regardless.
Far smaller harm than logging everybody out hourly.

See `HOTSPOT_KEEPALIVE` in `billing/router_profiles.py`.

---

## 9. Accounts and secrets nobody owns

**Symptom.** None visible. That is the problem.

**Why.** Deleting a customer used to remove the database row and nothing else.
Their account stayed on every router they had ever been provisioned on,
enabled, with nothing left to match it against. For PPPoE that is a working
username and password for unmetered internet.

Three were found live weeks after being deleted through the customers page.

**Confirm.**

```
/ppp secret print
/ip hotspot user print
```

Compare against the customers the database knows. Match **across all
operators** — a secret is matched by name alone and two operators can both have
a "john"; treating one's subscriber as the other's orphan disconnects somebody
who is paying.

**Fix.** `CustomerViewSet.perform_destroy` now clears the subscriber off every
router *before* deleting the row — it has to be that order, because
`disable_customer_access` reads the customer to know which usernames and which
routers to visit.

Two sweeps back it up, both two-step: disable and date-stamp first, remove
after 30 days. Disabling is reversible and deletion is not, so that gap is
where a mistake gets noticed.

- hotspot — `billing/management/commands/disable_orphan_hotspot_users.py`
- PPPoE — `billing/services/pppoe_orphans.py`

Disabling a PPPoE secret only refuses the *next* authentication and leaves an
established session running, so the live session must be ended alongside it.

---

## 10. Time-limited packages

`limit-uptime` counts **connected** time, not wall-clock time. A 3-hour package
must expire three hours after purchase whether the customer used it or not, so
the limit written to the router is:

```
banked uptime + current session + time remaining on the subscription
```

Sessions are **summed** across concurrent ones — `shared-users=2` allows two,
and both count. `billing/services/uptime_alignment.py` re-computes this every
five minutes.

The trap: an account that reports `uptime=0s` while plainly connected is
reporting the *account's* banked time, not the live session's. Reading it as
the whole story once produced a limit that would have cut off connected paying
customers by hours across 790 accounts.

---

## Standing configuration

Values that should be true on every router. All are asserted hourly; this is
the list to check by hand after a reset or a swap.

| Where | Setting | Value | Why |
|---|---|---|---|
| `/ip dhcp-server` (hotspot interface) | `lease-script` | the smartbill script | §3 |
| `/ip dhcp-server` | `lease-time` | `15m` | §3 — do not raise |
| `/ip dhcp-server config` | `store-leases-disk` | `5m` | leases survive reboot |
| `/ip hotspot walled-garden` | no public-resolver rules | — | §1 |
| `/ip hotspot walled-garden` | Billing API allowed | host + IP | portal must load |
| `/ip hotspot profile` | `login-by` | `mac,cookie,http-chap` | MAC re-auth |
| `/ip hotspot profile` | `mac-auth-mode` | `mac-as-username` | §4 |
| `/ip hotspot user profile` | `keepalive-timeout` | `none`, written explicitly | §8 |
| `/ip hotspot user profile` | `idle-timeout` | unset | §8 |
| `/interface pppoe-server server` | `keepalive-timeout` | `60` | §7 |
| `/ppp profile` (PPPOE_PKG_*) | `change-tcp-mss` | `yes` | §6 |
| `/ppp profile` (PPPOE_PKG_*) | `only-one` | `yes` | one session per account |

---

## Scheduled work that keeps this true

From `backend/settings.py`, `CELERY_BEAT_SCHEDULE`:

The five-minute tasks are offset by one minute each, deliberately: they all
open connections to the same routers, and running them together is how a sweep
stops finishing inside its own interval. A task that does not finish in time is
dropped rather than delayed — collection simply stops, and nothing reports it.

| Task | Minute | What it protects |
|---|---|---|
| `expire-subscriptions` | `*` | §10 |
| `clear-duplicate-sessions` | `0-59/5` | §2 |
| `align-uptime-limits` | `1-59/5` | §10 |
| `collect-hotspot-usage` | `2-59/5` | §4 |
| `sync-customer-status` | `3-59/5` | cap enforcement eligibility |
| `disable-orphan-hotspot-users` | `3-59/5` | §9 |
| `enforce-usage-caps` | `4-59/5` | §4, §5 |
| `ensure-lease-script` | `37` (hourly) | §1, §3, §7, §9 |

`ensure-lease-script` is the one that matters after any router work. Despite
the name it asserts four things: the lease script, the walled garden, the PPPoE
keepalive, and the PPPoE orphan sweep.

---

## How to scan the estate

The scripts used to find every fault above followed one shape: read the
router's own tables, and classify each device by **what it is actually
getting** rather than by what the database expects. The two disagreeing is the
entire fault class.

The buckets worth counting:

| Bucket | Meaning |
|---|---|
| `OK` | authorised and entitled |
| `PORTAL` | not authorised, not entitled — sees the signup page, correct |
| `STRANDED` | entitled, present, account fine, not authorised — §2 |
| `SILENT` | **authorised but cannot pass traffic** — the worst one |
| `FREE` | authorised with no entitlement |

`SILENT` matters most because a hotspot only redirects an *unauthorised* device
to the portal. A device the router considers logged in but whose traffic it is
dropping sees a browser that spins forever and is never told to buy anything.

Two refinements that stop false alarms:

- **Age floor.** A host entry seconds old is mid-handshake, not stuck. Use the
  same 600s floor the sweeps use, or new arrivals read as casualties.
- **Grant set, not customer.** A device is only stranded if it is one
  `macs_to_grant` would provision *today*. A phone that rotated its MAC is not
  the device the subscription granted — it has no account, is unauthorised, and
  is correctly shown the signup page.
