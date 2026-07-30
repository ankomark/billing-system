# Data-Model Spec — Phase 1 Multi-Tenancy

The contract Phase 1 gets checked against. Companion to `expansion plan.md`.

Derived by enumerating the codebase, not from memory: 19 models, 8 uniqueness
constraints, 16 indexes, 83 ORM call sites, 4 public endpoints.

**Status:** Draft 1 — spec only, no code written.

---

## How to use this

Phase 1 is done when every table in §2 has its column, every constraint in §3 is
changed, every index in §4 is rebuilt, and the acceptance tests in §9 pass.
Anything not listed here is out of scope for Phase 1.

---

## 1. The Tenant model

```python
class Tenant(models.Model):
    STATUS = (
        ("trial",      "Trial"),
        ("active",     "Active"),
        ("past_due",   "Past due"),
        ("restricted", "Restricted"),
        ("cancelled",  "Cancelled"),
    )

    name           = models.CharField(max_length=120)          # "Acme WiFi"
    slug           = models.SlugField(max_length=60, unique=True)
    status         = models.CharField(max_length=12, choices=STATUS, default="trial")

    # Public identity used in subscriber notifications, replacing hardcoded "Skylink"
    business_name  = models.CharField(max_length=120)
    support_phone  = models.CharField(max_length=20, blank=True)
    pppoe_prefix   = models.CharField(max_length=10, default="NET")  # replaces "SKY-"

    # Opaque token in per-tenant public URLs (M-Pesa callback, hotspot portal).
    # Not a secret in the credential sense, but unguessable so portals cannot be
    # pointed at the wrong operator by editing a URL.
    public_token   = models.CharField(max_length=32, unique=True, db_index=True)

    contact_email  = models.EmailField(blank=True)
    contact_phone  = models.CharField(max_length=20, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
```

`Tenant` is **not** itself tenant-scoped — it is the scope. Only platform staff
may list it; an operator may read only their own row.

---

## 2. Table-by-table

### 2.1 Gets `tenant` FK — 18 tables

`tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="+")`

`PROTECT`, not `CASCADE`: deleting a tenant must never silently destroy billing
history. Deactivate instead; deletion is a separate, deliberate procedure.

| Model | Notes |
|---|---|
| `RouterDevice` | **Highest priority.** Owner concept does not exist today. |
| `Customer` | |
| `Package` | Each operator prices independently |
| `Subscription` | Denormalised — derivable via Customer, needed on-row for RLS |
| `Invoice` | Denormalised |
| `Payment` | Denormalised |
| `Voucher` | Denormalised via Subscription |
| `RouterFailoverLog` | Denormalised via Customer |
| `ExpiryReminderLog` | Denormalised via Subscription |
| `AccessAuditLog` | Denormalised via Customer |
| `SystemSetting` | Becomes per-operator M-Pesa/SMS credentials |
| `MpesaTransaction` | Which operator's till the money hit |
| `PPPoEUsageSnapshot` | |
| `PPPoEUsageState` | |
| `PPPoEUsageRecord` | |
| `HotspotUsageState` | |
| `HotspotUsageRecord` | |
| `UsageRecord` | |

**On denormalisation:** several of these are reachable through a parent
(`Invoice → Customer → tenant`). Store `tenant_id` directly anyway. RLS policies
must not perform a join to decide visibility — a policy that joins is both slow
and, if the joined table is itself protected, circular.

### 2.2 Special case — `User`

```python
tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.PROTECT)
```

`NULL` means platform staff (you). Non-null means the user belongs to that
operator. Roles become:

| Role | Tenant | Sees |
|---|---|---|
| `platform_owner` | NULL | Everything, all operators |
| `platform_staff` | NULL | Everything, all operators |
| `tenant_admin` | set | Their operator only |
| `tenant_staff` | set | Their operator only |
| `customer` | set | Their own records only |

> **Open decision — username uniqueness.** `AbstractUser.username` is globally
> unique. Two operators will both want an `admin` account. Options:
>
> 1. **Keep global uniqueness** (recommended). Onboarding assigns
>    `acme-admin`, or usernames become email addresses. Zero auth changes.
> 2. **Scope to tenant.** Requires a custom authentication backend, a modified
>    JWT token serializer, and a login flow that knows the tenant before
>    authenticating (subdomain or an explicit field). Materially more work and
>    more ways to get authentication wrong.
>
> Recommendation: option 1 for Phase 1. Revisit only if operators complain.

### 2.3 Not tenant-scoped

`Tenant` itself, plus Django's own tables (`auth_*`, `django_*`,
`django_celery_beat_*`, `django_celery_results_*`).

Celery beat schedules stay global — the scheduled tasks iterate tenants
internally rather than being duplicated per tenant.

---

## 3. Uniqueness constraints

| # | Current | Change to | Why |
|---|---|---|---|
| 1 | `Customer.phone` unique | `unique_together(tenant, phone)` | One person may subscribe to two operators |
| 2 | `Customer.hotspot_username` *(no constraint)* | `unique_together(tenant, hotspot_username)` | See §6 — MAC collisions are a live correctness bug |
| 3 | `Customer.pppoe_username` *(code-enforced only)* | `unique_together(tenant, pppoe_username)` | Uniqueness currently relies on a race-prone `.exists()` check with no DB constraint |
| 4 | `SystemSetting.key` unique | `unique_together(tenant, key)` | Per-operator credentials |
| 5 | `Invoice.invoice_number` unique | **unchanged — keep global** | M-Pesa callback resolves tenant from it |
| 6 | `Voucher.code` unique | **unchanged — keep global** | Hotspot portal resolves tenant from it |
| 7 | `MpesaTransaction.mpesa_receipt` unique | **unchanged — keep global** | Safaricom receipts are globally unique; keeps idempotency correct |
| 8 | `ExpiryReminderLog(subscription, reminder_type)` | unchanged | Subscription already implies tenant |
| 9 | `PPPoEUsageSnapshot(customer, date)` | unchanged | Customer implies tenant |
| 10 | `UsageRecord(customer, date, connection_type)` | unchanged | Customer implies tenant |

Constraints 2 and 3 need `condition=~Q(field="")` — both columns are `blank=True`
and many rows legitimately hold `""`.

---

## 4. Indexes

Every existing index must be rebuilt with `tenant` **leading**. After Phase 2
every query carries a tenant predicate, so an index that does not start with
`tenant_id` cannot be used efficiently.

| Model | Current | Becomes |
|---|---|---|
| `Customer` | `(status)` | `(tenant, status)` |
| `Customer` | `(pppoe_username)` | `(tenant, pppoe_username)` |
| `Customer` | `(connection_type)` | `(tenant, connection_type)` |
| `Subscription` | `(status)` | `(tenant, status)` |
| `Subscription` | `(expiry_date)` | `(tenant, expiry_date)` |
| `Subscription` | `(status, expiry_date)` | `(tenant, status, expiry_date)` |
| `Invoice` | `(payment_status)` | `(tenant, payment_status)` |
| `Invoice` | `(created_at)` | `(tenant, created_at)` |
| `Payment` | `(paid_at)` | `(tenant, paid_at)` |
| `Payment` | `(method)` | `(tenant, method)` |
| `Payment` | `(paid_at, method)` | `(tenant, paid_at, method)` |
| `MpesaTransaction` | `(status)` | `(tenant, status)` |
| `MpesaTransaction` | `(processed)` | `(tenant, processed)` |
| `MpesaTransaction` | `(status, processed)` | `(tenant, status, processed)` |
| `MpesaTransaction` | `(created_at)` | `(tenant, created_at)` |
| `PPPoEUsageRecord` | `(customer, period_start)` | unchanged — customer implies tenant |
| `HotspotUsageRecord` | `(customer, period_start)` | unchanged — customer implies tenant |

One exception: `Subscription(status, expiry_date)` is also read by
`enforce_subscription_expiry`, which sweeps **all** tenants. Keep the existing
non-tenant index alongside the new one so that sweep stays fast.

---

## 5. Query scoping inventory

83 ORM call sites outside tests and migrations.

| File | Sites | Risk |
|---|---|---|
| `views.py` | 29 | Scoped automatically by the default manager once the request carries a tenant |
| `reports.py` | 7 | **Must be explicit** — pure aggregates with no tenant predicate |
| `router_service.py` | 6 | **Critical** — see below |
| `reconcile_mpesa.py` | 4 | Management command, no request context |
| `tasks/*` | 16 | **No request context** — every task needs an explicit tenant |
| `dashboards.py` | 3 | Must be explicit |
| `models.py` | 3 | Inside `save()`; inherits the instance's tenant |
| `services/*` | 2 | Public entry points; see §6 |
| `management/commands/*` | 3 | No request context |
| `tasks_usage.py`, `tasks_usage_hotspot.py` | 6 | Legacy, superseded by `tasks/usage_tasks.py` — **delete rather than port** |
| `config.py` | 1 | `get_setting()` — see §5.3 |

### 5.1 Router selection — 15 sites, highest severity

Every one of these scans routers platform-wide with no owner concept:

```
router_service.py:284   pick_working_router()
router_service.py:304   pick_failover_router()
router_service.py:334   pick_best_router_for_new_customer()
router_service.py:449   get_pppoe_live_usage_any_router()
router_service.py:489   get_hotspot_live_usage_any_router()
tasks/auto_failover.py:23      run_auto_failover_task()
tasks/router_health.py:23      check_router_health_task()
tasks/__init__.py:69,76        run_failover_cycle()
views.py:934,1012,1063,1094,1156
management/commands/router_failover.py:12
```

Consequence if missed: Operator A's subscriber gets provisioned onto Operator B's
MikroTik. That is a physical misconfiguration on someone else's hardware, not
merely a data leak, and RLS will not save you — the query is legitimate SQL
issued in a valid tenant context, just with the wrong candidate set.

**Every router-selection function takes an explicit `tenant` argument. No defaults.**

### 5.2 Platform-wide sweeps in scheduled tasks

`enforce_subscription_expiry`, `send_expiry_reminders`,
`collect_pppoe_usage_snapshots`, `run_auto_failover_task`,
`check_router_health_task` and `dispatch_broadcast_task` all iterate globally by
design and should continue to — but each must:

1. Iterate tenants explicitly, setting tenant context per iteration
2. **Skip `restricted` and `cancelled` tenants** for provisioning actions
3. Use that tenant's credentials for any notification it sends

### 5.3 `get_setting()` cache poisoning

`config.py` builds its Redis key as `f"sys_setting:{key}"` with no tenant
component. Once settings are per-tenant, Operator A's `MPESA_CONSUMER_SECRET`
would be served from cache to Operator B.

That is a **credential leak, not a data leak** — the more serious of the two,
and RLS does not protect it because the value never touches the database on a
cache hit.

```python
def _cache_key(tenant_id: int, key: str) -> str:
    return f"sys_setting:{tenant_id}:{key}"
```

`clear_settings_cache()` must take a tenant and purge only that tenant's keys.

---

## 6. Public endpoints — tenant resolution

Four endpoints are `AllowAny`, so there is no JWT and no tenant context.

| Endpoint | Resolves via | Safe? |
|---|---|---|
| `MpesaSTKCallbackView` | `invoice_number` (globally unique) | ✅ |
| `HotspotVoucherValidateView` | voucher `code` (globally unique) | ✅ |
| `HotspotStatusView` | **MAC address alone** | ❌ |
| `HotspotReconnectView` | **MAC address alone** | ❌ |

### The MAC problem

```python
# views.py:543  and  views.py:1665
customer = Customer.objects.filter(hotspot_username=mac).first()
```

`hotspot_username` holds a device MAC and carries **no uniqueness constraint at
all**. Two operators can trivially have a subscriber with the same MAC — same
handset model batches, a spoofed address, or a genuine device that moved between
operators.

`.first()` then returns whichever row the database happens to hand back. Two
distinct failures follow: one operator's subscriber status is disclosed to
another, and access may be granted or denied against the wrong subscription.

This is arguably a latent bug **today** — nothing stops two subscribers of the
current single tenant sharing a MAC — but multi-tenancy turns it from unlikely
into routine.

**Fix, both layers:**

1. `unique_together(tenant, hotspot_username)` so the ambiguity cannot exist
   within a tenant.
2. Public hotspot endpoints take a tenant token:
   `/api/hotspot/status/?mac=<mac>&t=<tenant.public_token>`

   The MikroTik login page is already per-operator — each configures its own
   `API_BASE` in `login.html` — so embedding the token there costs nothing at
   deployment time. Lookups become `filter(tenant=t, hotspot_username=mac)`.

Same treatment for the M-Pesa callback: move to
`/api/mpesa/callback/<tenant.public_token>/`. Invoice lookup already
disambiguates correctly, but a per-tenant URL means the wrong operator's
credentials are never even loaded.

---

## 7. RLS policy specification

Applied to all 18 tables in §2.1.

```sql
ALTER TABLE billing_customer ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_customer FORCE  ROW LEVEL SECURITY;   -- ← without this it does nothing

CREATE POLICY tenant_isolation ON billing_customer
    USING (tenant_id = current_setting('app.current_tenant_id')::int);
```

Three requirements, all of which are easy to get wrong:

1. **`FORCE`.** Django connects as the table owner, and owners bypass RLS
   without it. Omit it and you have the appearance of protection and none of the
   substance.
2. **`SET LOCAL`, inside a transaction.** `CONN_MAX_AGE=60` means connections
   are pooled across requests; a plain `SET` persists and the next request
   inherits the previous tenant's context.
3. **A platform-staff escape.** Cross-tenant dashboards must read everything.
   Use a `BYPASSRLS` role for those queries — never by disabling the policy.

```python
@contextmanager
def tenant_context(tenant_id):
    with transaction.atomic():
        with connection.cursor() as c:
            c.execute("SELECT set_config('app.current_tenant_id', %s, true)",
                      [str(tenant_id)])   # true = transaction-local
        yield
```

---

## 8. Migration sequence

Each step is separately deployable and reversible.

| # | Step | Reversible |
|---|---|---|
| 1 | Create `Tenant`; add nullable `tenant` FK everywhere | Yes |
| 2 | Data migration: create tenant #1 "Skylink", claim all existing rows | Yes, with care |
| 3 | Verify zero `NULL`s in every column | — |
| 4 | `ALTER COLUMN … SET NOT NULL` | Yes |
| 5 | Swap uniqueness constraints (§3) | Yes |
| 6 | Rebuild indexes `CONCURRENTLY` (§4) | Yes |
| 7 | Enable RLS + `FORCE` (§7) | Yes |

Step 2 is the only irreversible-in-practice step, because after it runs, new
rows arrive carrying tenant IDs. **Rehearse against a restored production dump
before it goes anywhere near live data.**

Step 6 must use `CREATE INDEX CONCURRENTLY`; a plain `CREATE INDEX` takes an
`ACCESS EXCLUSIVE` lock and will stall the API. Django needs
`atomic = False` on that migration for concurrent index creation to work.

---

## 9. Acceptance tests

> **Correction to draft 1.** This section originally said "Phase 1 is not done
> until these pass" and then listed all seven. That was wrong: tests 1–6 all
> require query scoping and RLS, which are phase 2. Only test 7 is achievable
> from the phase 1 data-model work alone. Phases 1 and 2 were therefore
> delivered together — a tenant column with no scoping is the worst of both
> worlds, since it looks multi-tenant while isolating nothing.
>
> | Test | Gate |
> |---|---|
> | 7. Backfill integrity | Phase 1 |
> | 1–6 | Phase 2 |

1. **Isolation sweep.** For every model in §2.1, create identical rows under two
   tenants, authenticate as tenant A, and assert every list and detail endpoint
   returns only A's rows. Table-driven, so a new model added without a
   `tenant` FK fails the suite automatically.
2. **RLS actually engaged.** With `app.current_tenant_id` set to A, a **raw SQL**
   `SELECT COUNT(*)` on each table returns only A's rows. This is what catches a
   missing `FORCE`.
3. **Router isolation.** `pick_best_router_for_new_customer(tenant=A)` never
   returns a router belonging to B, even when B's routers are healthier and
   higher priority.
4. **Settings cache.** Write `MPESA_CONSUMER_SECRET` for A and B; read back as
   each; assert no bleed. Run twice so the second pass is served from cache.
5. **MAC collision.** Two tenants, one MAC. Assert `/api/hotspot/status/`
   returns the correct subscriber for each tenant token, and never the other's.
6. **Connection reuse.** Two sequential requests as different tenants on the
   same pooled connection. Asserts `SET LOCAL` rather than `SET`.
7. **Backfill integrity.** Post-migration, zero `NULL` tenant IDs, and every
   child's tenant matches its parent's — no `Invoice` under a different tenant
   from its `Customer`.

---

## 10. Out of scope for Phase 1

Platform billing models, suspension state machine, master dashboard, frontend
restructure, and per-tenant branding rollout. Phase 1 delivers the column, the
constraints, the indexes and the isolation guarantee — nothing else.

Deleting `tasks_usage.py` and `tasks_usage_hotspot.py` (superseded legacy, 6 ORM
sites) is worth folding in, since porting them to be tenant-aware would be wasted
effort.
