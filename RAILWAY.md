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

## 0. How four services share one repository

Every app service points at the same GitHub repo and is told which part of it to
look at. Three settings do that, and **they do not agree on what a path is
relative to** — which is the one thing here that will waste an afternoon if you
assume it.

| Setting | Relative to | web / worker / beat | frontend |
|---|---|---|---|
| Root Directory | repo root | `backend` | `frontend/wifi-billing-frontend` |
| Config-as-code path | **repo root, always** | `/backend/railway.web.json` etc. | — |
| Watch Paths | **repo root, always** | `/backend/**` | `/frontend/**` |

**Root Directory** is the answer to your question: Railway pulls down only that
directory when it builds. So for the backend services the build context *is*
`backend/`, `Dockerfile` sits at the top of it and is auto-detected, `COPY . .`
copies the Django project and nothing else, and `backend/.dockerignore` applies.
This is byte-for-byte the context compose builds from (`build: .`, in that same
directory), so the image you test locally is the image Railway runs. The
frontend service pulls down only the CRA app and never sees the Python.

**Config-as-code does not follow Root Directory.** Railway's own docs are
explicit about this, and it is the one asymmetry in the table: the path must be
written from the repo root, leading slash and all. Give it `railway.web.json`
and Railway looks for that file at the top of the repository, does not find it,
silently falls back to auto-detection, and starts the container on the
Dockerfile's default `CMD` — which is gunicorn. Your *worker* then runs a web
server instead of Celery, comes up green, and processes no tasks.

**Watch Paths** are worth the two minutes. Without them every push redeploys all
four services, so a CSS tweak rebuilds and restarts Celery. Set them under
Settings → Source.

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
| Config-as-code path | `/backend/railway.web.json` |
| Watch Paths | `/backend/**` |

Mind the leading slash on the config path — see [section 0](#0-how-four-services-share-one-repository).

That file carries the builder, the start command, `/health/` as the healthcheck
and `migrate --noinput` as the pre-deploy step, so there is nothing to type into
the start-command box. Confirm it was actually read: the build log says
`Using detected Dockerfile!` and the deploy log's first line is gunicorn
binding. If instead you see a Nixpacks plan, the path is wrong.

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

Two more services from the same repo, same Root Directory (`backend`), same
watch path (`/backend/**`), same variables. Only the config path differs:

| Service | Config-as-code path | Replicas |
|---|---|---|
| worker | `/backend/railway.worker.json` | 1 |
| beat | `/backend/railway.beat.json` | **1, never more** |

Neither needs a domain — do not generate one.

Check the deploy log of each says `celery@…ready` or `beat: Starting…`. A worker
that logs `Listening at: http://0.0.0.0:8080` did not read its config file and
is running the Dockerfile's default gunicorn instead. It will look healthy
indefinitely.

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
| Root Directory | `frontend/wifi-billing-frontend` |
| Watch Paths | `/frontend/**` |
| Start command | `npm run serve` |
| `REACT_APP_API_URL` | `https://<web>.up.railway.app/api/` — with the trailing slash |

No config file for this one — Nixpacks sees `package.json` at the top of its
root directory, installs and runs `npm run build` on its own.

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
