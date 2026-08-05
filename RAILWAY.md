# Railway — the trial deployment

A throwaway environment to prove the stack works end to end before it moves to
Hetzner. `GUIDE.md` is the real one; this is the shortcut.

Six services: **web**, **worker**, **beat**, **Postgres**, **Redis**,
**frontend**. All three app services build the same `backend/Dockerfile` from
the same repo and differ only in their start command.

Read [What this trial cannot prove](#what-this-trial-cannot-prove) before you
promise anything to an operator. Two of the three moving parts of this system
behave differently here than they will on a VPS.

---

## 1. Postgres and Redis first

New Project → **Deploy PostgreSQL**, then **+ New** → **Deploy Redis**. Nothing
to configure on either.

### Then create the app role, before anything migrates

Railway hands you `postgres`, a superuser, and **a superuser bypasses Row-Level
Security unconditionally**. Connect Django as it and every `tenant_isolation`
policy in the database is inert while still listed in `pg_policies` exactly as
though it were working — one operator's dashboard is then one missing
`.filter()` away from another operator's customers.

Generate a password:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open the Postgres service → **Data** → **Query**, and run
`backend/docker/railway-app-role.sql` with that password substituted. It ends
with a `SELECT` whose two columns must both read `f`.

Do this **before** the first deploy. Migration 0030 applies
`FORCE ROW LEVEL SECURITY` to tables it assumes Django owns, and a table is
owned by whoever created it.

---

## 2. The web service

**+ New** → **GitHub Repo** → `ankomark/billing-system`.

Settings:

| Field | Value |
|---|---|
| Root Directory | `backend` |
| Config-as-code path | `railway.web.json` |

That file carries the builder, the start command, `/health/` as the healthcheck
and `migrate --noinput` as the pre-deploy step, so there is nothing to type into
the start-command box.

### Variables

Set these on **web**, then copy the whole block to **worker** and **beat** —
they run the same code and need the same configuration.

```ini
SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(64))">
FIELD_ENCRYPTION_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
DEBUG=False

POSTGRES_DB=railway
POSTGRES_USER=wifi_app
POSTGRES_PASSWORD=<the password from step 1>
POSTGRES_HOST=${{Postgres.RAILWAY_PRIVATE_DOMAIN}}
POSTGRES_PORT=5432

REDIS_URL=${{Redis.REDIS_URL}}

GUNICORN_WORKERS=2
ENVIRONMENT=staging
MPESA_ENV=sandbox
MPESA_ALLOW_LOCAL_CALLBACK=False
```

`${{Postgres.…}}` is Railway's own reference syntax — type it literally and it
resolves at deploy. Use the **private** domain: the public one leaves the
datacentre and back, and you pay egress for every query.

`POSTGRES_USER` is `wifi_app`, not `postgres`. That is the entire point of
step 1.

Two more once the service has a URL — generate a domain under
**Settings → Networking → Generate Domain**, then:

```ini
PLATFORM_BASE_URL=https://<web>.up.railway.app
CORS_ALLOWED_ORIGINS=https://<frontend>.up.railway.app
```

`ALLOWED_HOSTS` needs nothing: settings.py reads `RAILWAY_PUBLIC_DOMAIN`, which
Railway injects, and adds it along with the matching `CSRF_TRUSTED_ORIGINS`
entry.

Leave `SECURE_SSL_REDIRECT` unset. Railway terminates TLS and forwards
`X-Forwarded-Proto`, which settings.py now trusts, so the redirect is correct
rather than a loop.

---

## 3. worker and beat

Two more services from the same repo, same root directory, same variables. Only
the config path differs:

| Service | Config-as-code path | Replicas |
|---|---|---|
| worker | `railway.worker.json` | 1 |
| beat | `railway.beat.json` | **1, never more** |

Neither needs a domain — do not generate one.

**Beat must stay at one replica.** Two schedulers means every periodic task
fires twice: two expiry sweeps, two invoice runs, two reminder SMS to the same
customer. The worker can scale; the scheduler cannot.

On the very first deploy, worker and beat may crash-loop for a minute while web's
pre-deploy migration is still creating tables. `ON_FAILURE` restarts them and
they settle on their own.

---

## 4. Frontend

**+ New** → same repo, Root Directory `frontend/wifi-billing-frontend`.

| Field | Value |
|---|---|
| Start command | `npm run serve` |
| `REACT_APP_API_URL` | `https://<web>.up.railway.app/api/` — with the trailing slash |

Create-React-App bakes environment variables into the bundle at build time, so
changing that variable requires a **redeploy**, not a restart. If the dashboard
loads but every request goes to `127.0.0.1:8000`, this is why.

---

## 5. Prove it

```bash
curl https://<web>.up.railway.app/health/
# {"status":"ok","checks":{"db":"ok","redis":"ok"}}
```

`"redis":"ok"` matters as much as the db line. A degraded cache still returns
HTTP 200 — the endpoint is deliberately forgiving — so read the body, not the
status code.

Then, from the web service's shell (**Deployments → ⋮ → Shell**):

```bash
python manage.py create_platform_owner
```

Sign in to the frontend with it. Then check that beat is alive — its log should
show scheduler entries every couple of minutes, and `check-router-health` is the
noisiest, so it appears first. Beat dying is the one silent failure in this
stack: no scheduler means no expiry sweeps, no reminders, no failover, and
nothing anywhere reports an error.

---

## What this trial cannot prove

**Reaching an operator's router.** The backend dials out to each MikroTik on
port 8728 (`RouterDevice.api_port`). Railway allows the outbound connection, but
its egress addresses are shared and not stable, so an operator who firewalls
their API port to a specific source IP cannot allowlist you — and telling them
to open 8728 to the internet is not an acceptable substitute. A fixed IP is
exactly what the Hetzner box buys. If router control works here, it proves the
protocol; it does not prove the deployment.

**Live M-Pesa.** Sandbox callbacks reach a `railway.app` URL fine. A production
shortcode is registered against one callback URL, and re-registering it is
Safaricom's timeline, not yours — so point production at the hostname you intend
to keep, never at this one.

**Cost under load.** Six services on usage-based pricing is fine for a trial and
is not what a steady-state estate should cost. That comparison belongs after the
move, not before it.

The portal files in `mikrotik-hotspot/` are uploaded to each router by hand and
are unaffected by any of this — only `config.js` changes, to point at the URL
above.

---

## Moving to Hetzner afterwards

Nothing here is a fork of the deployment. The Railway config files are additive,
compose still works untouched, and the three settings changes this needed —
trusting `X-Forwarded-Proto`, exempting `/health/` from the HTTPS redirect,
reading the hostname from the environment — are what any reverse proxy in front
of gunicorn wants, including the one `GUIDE.md` sets up.

What does not transfer: the database. Dump it and restore, or start clean —
`FIELD_ENCRYPTION_KEY` must move with the data or every stored router password
becomes unreadable.
