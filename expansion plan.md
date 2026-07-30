# Expansion Plan — Multi-Tenant Platform

Migrating Skylink from a single ISP's billing system into a platform hosting many
independent WiFi operators, each with isolated data, their own M-Pesa till, and a
monthly bill owed to the platform owner.

**Status:** Draft 1 — planning only, no code written yet.

---

## Decisions locked

| Decision | Choice |
|---|---|
| Data isolation | Shared schema + Postgres Row-Level Security |
| Subscriber payments | Each operator uses their own M-Pesa till |
| Non-payment response | Lock operator out; subscribers keep internet |
| Expected scale | 10–50 operators in year one |

---

## 1. What we're building

Three parties. The distinction between them drives every decision below.

| Party | Who they are | Pays |
|---|---|---|
| **Platform** | You. One master dashboard across every operator. | — |
| **Operator** (tenant) | An ISP running hotspot/PPPoE. Own admins, customers, packages, routers. | You, monthly |
| **Subscriber** | The end customer buying WiFi. | Their operator, into that operator's till |

> **The thing to hold onto:** there are **two entirely separate billing layers**.
> The existing `Invoice` / `Payment` / `Subscription` models bill subscribers. A
> new, deliberately distinct set bills operators. Most of the design difficulty
> in this project comes from conflating the two — so we never reuse a model
> across that boundary.

---

## 2. Isolation: shared schema, hardened with RLS

Every tenant-scoped table gets a `tenant_id` column. One database, one set of migrations.

| Approach | Cross-tenant reporting | Migrations | Verdict |
|---|---|---|---|
| Shared schema | Trivial — one `GROUP BY` | One set | **Chosen** |
| Schema per tenant | Must query N schemas | Per schema; slow past ~50 | Rejected |
| Database per tenant | Very painful | Unmanageable solo | Rejected |

The master dashboard is a cross-tenant aggregate by definition — "who hasn't paid,
who has how many subscribers". Schema-per-tenant makes precisely that query the
hardest thing in the system, and doubles migration work for a solo developer.

### Three layers of defence

1. A `TenantScopedModel` base with a default manager that **auto-filters**, so the
   safe path is the default and reaching across tenants requires explicitly
   typing `.all_tenants()`.
2. Middleware setting the current tenant from the JWT into a context variable.
3. **Postgres Row-Level Security** — the database refuses cross-tenant rows even
   when application code is wrong.

### ⚠️ Two ways RLS silently does nothing

**Table owners bypass RLS.** Django normally connects as the role that owns the
tables, and owners ignore policies unless you run:

```sql
ALTER TABLE billing_customer FORCE ROW LEVEL SECURITY;
```

Skip that one statement and you have the appearance of protection with none of
it. The migration must assert it, and a test must prove a leak is actually blocked.

**Connection reuse leaks context.** `CONN_MAX_AGE=60` means connections are shared
across requests. Set the tenant with `SET LOCAL` inside a transaction — a plain
`SET` persists on the pooled connection and the next request inherits the
previous tenant's context.

---

## 3. What breaks in the code as it stands

Checked against the current codebase, not hypothetical.

### Router selection crosses tenants — the most serious

`pick_best_router_for_new_customer()` in `router_service.py` scans
`RouterDevice.objects.filter(is_active=True)` with no owner concept. Today, when
Operator A's subscriber pays, this could provision them onto **Operator B's
MikroTik**.

Same flaw in:

- `pick_working_router()`
- `pick_failover_router()`
- `AdminPPPoESessionsView`
- `run_auto_failover_task`

This has physical consequences, not just data ones.

### `Customer.phone` is globally unique

```python
phone = models.CharField(max_length=20, unique=True)
```

One person cannot be a subscriber of two operators on the platform. Becomes
`unique_together("tenant", "phone")`.

### `SystemSetting` is one global key/value store

It holds `MPESA_CONSUMER_KEY`, `MPESA_SHORTCODE`, `AT_API_KEY` — so it *is* the
mechanism for "payments go to their till". It must become tenant-scoped, and
`get_setting()`'s Redis cache key must include the tenant ID. Miss that and
operators read each other's M-Pesa credentials out of cache: a credential leak,
not merely a data leak.

You also need a separate **platform-level** settings store for your own till —
the one that collects operator subscriptions. Different scope, different model.

### Brand is hardcoded in the money path

`Payment.save()` sends `"Welcome to Skylink WiFi!"` and `"Support: 0700 XXX XXX"`.
Same in `onboarding.py` and `Subscription.save()`. Every operator's subscribers
would receive SMS branded as Skylink.

Needs per-tenant business name, support number and PPPoE username prefix —
`generate_pppoe_credentials()` hardcodes `SKY-`.

### Already correct — leave alone

`invoice_number` and `Voucher.code` are globally unique. **Keep them that way:**
it lets the M-Pesa callback resolve which tenant a payment belongs to purely by
invoice lookup, which solves an otherwise awkward routing problem for free.

---

## 4. Phases

Ordered so each phase leaves the system working and deployable. Sizes are
relative, not commitments.

### Phase 1 — Tenant model and backfill `[M]`

Create `Tenant` (name, slug, status, branding, contact). Add the `tenant` FK
across Customer, Package, RouterDevice, Subscription, Invoice, Payment, Voucher,
MpesaTransaction, SystemSetting, and the usage and log models.

A data migration creates Skylink as tenant #1 and claims all existing rows,
*then* the column becomes non-nullable.

Denormalise `tenant` onto Subscription/Invoice/Payment even though it is
derivable through Customer — RLS policies and indexes both need it directly on
the row.

### Phase 2 — Scoping, RLS and the isolation test `[M]`

Context variable, middleware, auto-filtering manager, RLS policies with
`FORCE ROW LEVEL SECURITY`, and a test that loops over every tenant-scoped model
asserting tenant A cannot reach tenant B through the API. That test is worth more
than any amount of code review.

**Celery is the trap.** Tasks run with no request, so no middleware sets the
tenant. Every task takes an explicit `tenant_id` or runs inside an explicit
per-tenant loop.

### Phase 3 — Per-tenant configuration and payments `[M]`

Tenant-scoped settings with tenant-aware cache keys. Per-tenant Daraja
credentials so subscriber money lands in the operator's till. Per-tenant callback
URLs (`/api/mpesa/callback/<tenant-token>/`) rather than relying on invoice lookup
alone. A "payments not yet configured" state for operators still waiting on
Safaricom.

### Phase 4 — Users, roles, permissions `[M]`

`User.tenant`, null for platform staff. Roles become `platform_owner`,
`platform_staff`, `tenant_admin`, `tenant_staff`, `customer`. JWT carries a
`tenant_id` claim. New permission classes replace the current `IsAdmin`.

### Phase 5 — Platform billing `[L]`

The second billing layer: `PlatformPlan`, `TenantSubscription`, `TenantInvoice`,
`TenantPayment`.

Distinct names on purpose — **do not reuse `Invoice`/`Payment`**, or you will
confuse them at 2am while debugging a payment. Monthly invoice generation,
collection via your till, plan limits on subscriber and router counts.

### Phase 6 — Suspension and enforcement `[M]`

Tenant status machine: `trial` → `active` → `past_due` → `restricted`. Grace
period with reminders at days 3, 7 and 14 before anything restricts. See §5 — the
code is easy, the policy is not.

### Phase 7 — Master dashboard `[M]`

Operator list with health, MRR, per-operator subscriber counts and revenue,
overdue platform invoices, router status rollup.

Plus **impersonation** — "view as this operator" — which is invaluable for
support and must be audit-logged on every use.

### Phase 8 — Frontend restructure `[L]`

A new `/platform/*` route tree alongside the existing `/admin/*`. The current 18
admin pages largely work unchanged, because the API scopes them automatically
once phase 2 lands — that is the payoff for doing isolation properly at the data
layer rather than in the UI.

### ⚠️ Sequencing constraint

Phases 1 through 4 must **all** land before any of this is usable. There is no
partial multi-tenancy — a half-scoped system is a leaking system. Plan that as
one block of work, not four independently shippable pieces.

---

## 5. Suspension: the chosen policy, and one refinement

The choice is to lock the operator out while leaving their subscribers connected.
That is the right instinct — those subscribers paid their ISP in good faith and
did nothing wrong, and some depend on that connection for work or emergencies.
Cutting them off to pressure someone else is a poor trade.

**But there is a hole worth closing.** If *everything automated* keeps running, a
restricted operator loses only their dashboard while renewals, provisioning and
payments continue landing in their till. That is weak leverage — they could
ignore the invoice indefinitely and barely feel it.

The balanced position:

- Block operator logins **and** freeze new subscriber creation and new provisioning
- Existing subscribers keep service; renewals keep working
- Their business stops growing without anyone losing internet they already paid for
- Escalation beyond that stays a deliberate manual action, never automatic

---

## 6. Risks, ranked

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Cross-tenant data leak** | Business-ending. One operator sees another's subscriber list. | RLS with FORCE, auto-filtering manager, isolation test suite |
| **Router cross-provisioning** | Physical consequences — a subscriber lands on another operator's hardware. | Scope every router query in phase 1; test explicitly |
| **The migration itself** | Live production data, and the backfill is not trivially reversible. | Rehearse on a restored copy; keep a rollback path |
| **M-Pesa onboarding** | Each operator needs their own Daraja app, shortcode and passkey — manual and Safaricom-gated. | Likely the real bottleneck; design onboarding to work while pending |
| **Scope** | This is a rewrite of the data layer, not a feature. | Treat phases 1–4 as one indivisible block |

---

## 7. Where to start

Before any code: a written **data-model spec** naming

- every table that gets `tenant_id`
- every uniqueness constraint that changes
- every query in the current codebase that must be scoped

That document is the contract phase 1 gets checked against — cheap to write now,
expensive to reconstruct once the migration is half-applied.

Then phase 1 on a branch, rehearsed against a copy of production data before it
goes anywhere near the live database.

---

## Related

- Web version of this plan: https://claude.ai/code/artifact/ac234c92-eef9-43b5-bc26-b6595fd9b058
- Prior cleanup work: PR #1 (dead code, Celery task registration), PR #2 (gunicorn, Flower auth)
