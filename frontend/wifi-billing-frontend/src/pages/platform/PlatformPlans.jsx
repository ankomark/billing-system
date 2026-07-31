import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Tag } from "lucide-react";
import toast from "react-hot-toast";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { Card, PageHeader, KES, num } from "../../components/platform/ui";
import {
  createPlan,
  fetchPlatformPlans,
  updatePlan,
} from "../../services/platform";
import { getUser } from "../../services/auth";
import { PLATFORM_OWNER } from "../../constants/roles";

/**
 * The plan catalogue.
 *
 * The API has supported this since platform billing landed; the only UI that
 * ever touched it was the dropdown on the onboarding form. So a price could be
 * set once, at creation, and never changed without going into the database —
 * in a product whose whole purpose is billing.
 */
export default function PlatformPlans() {
  const qc = useQueryClient();
  const isOwner = getUser()?.role === PLATFORM_OWNER;
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  const [errors, setErrors] = useState({});

  const blank = {
    name: "", slug: "", price: "", billing_period_days: 30,
    max_customers: 0, max_routers: 0, is_active: true,
  };
  const [form, setForm] = useState(blank);

  const { data: plans = [], isLoading } = useQuery({
    queryKey: ["platform-plans"],
    queryFn: fetchPlatformPlans,
  });

  const done = (msg) => {
    toast.success(msg);
    setAdding(false);
    setEditing(null);
    setForm(blank);
    setErrors({});
    qc.invalidateQueries({ queryKey: ["platform-plans"] });
  };

  const create = useMutation({
    mutationFn: createPlan,
    onSuccess: () => done("Plan created"),
    onError: (e) => handleError(e, setErrors),
  });

  const update = useMutation({
    mutationFn: ({ id, payload }) => updatePlan(id, payload),
    onSuccess: () => done("Plan updated"),
    onError: (e) => handleError(e, setErrors),
  });

  const startEdit = (plan) => {
    setEditing(plan.id);
    setAdding(false);
    setErrors({});
    setForm({
      name: plan.name,
      slug: plan.slug,
      price: plan.price,
      billing_period_days: plan.billing_period_days,
      max_customers: plan.max_customers,
      max_routers: plan.max_routers,
      is_active: plan.is_active,
    });
  };

  const submit = (e) => {
    e.preventDefault();
    const payload = { ...form };
    if (editing) update.mutate({ id: editing, payload });
    else create.mutate(payload);
  };

  const err = (k) => (Array.isArray(errors[k]) ? errors[k].join(" ") : errors[k]);

  return (
    <PlatformLayout>
      <div className="space-y-6 max-w-4xl">
        <PageHeader
          title="Plans"
          subtitle="What operators are charged, and what each plan allows"
        >
          {isOwner && (
            <button
              onClick={() => {
                setAdding((v) => !v);
                setEditing(null);
                setForm(blank);
              }}
              className="inline-flex items-center gap-2 bg-teal-500 hover:bg-teal-400 text-slate-950 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
            >
              <Plus size={16} />
              New plan
            </button>
          )}
        </PageHeader>

        {(adding || editing) && (
          <Card>
            <form onSubmit={submit} className="space-y-4">
              <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">
                {editing ? "Edit plan" : "New plan"}
              </p>

              <div className="grid sm:grid-cols-2 gap-4">
                <Field label="Name" error={err("name")}>
                  <input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Starter"
                    className={inputCls(err("name"))}
                  />
                </Field>
                <Field
                  label="Slug"
                  hint="Used by the API. Leave alone once operators are on it."
                  error={err("slug")}
                >
                  <input
                    value={form.slug}
                    onChange={(e) => setForm({ ...form, slug: e.target.value })}
                    placeholder="starter"
                    className={inputCls(err("slug"))}
                  />
                </Field>
                <Field label="Price (KES)" error={err("price")}>
                  <input
                    type="number"
                    step="0.01"
                    value={form.price}
                    onChange={(e) => setForm({ ...form, price: e.target.value })}
                    className={inputCls(err("price"))}
                  />
                </Field>
                <Field label="Billing period (days)" error={err("billing_period_days")}>
                  <input
                    type="number"
                    value={form.billing_period_days}
                    onChange={(e) =>
                      setForm({ ...form, billing_period_days: e.target.value })
                    }
                    className={inputCls(err("billing_period_days"))}
                  />
                </Field>
                <Field label="Max subscribers" hint="0 means unlimited" error={err("max_customers")}>
                  <input
                    type="number"
                    value={form.max_customers}
                    onChange={(e) => setForm({ ...form, max_customers: e.target.value })}
                    className={inputCls(err("max_customers"))}
                  />
                </Field>
                <Field label="Max routers" hint="0 means unlimited" error={err("max_routers")}>
                  <input
                    type="number"
                    value={form.max_routers}
                    onChange={(e) => setForm({ ...form, max_routers: e.target.value })}
                    className={inputCls(err("max_routers"))}
                  />
                </Field>
              </div>

              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                  className="rounded border-white/20 bg-slate-900 text-teal-500 focus:ring-teal-500"
                />
                Offered to new operators
              </label>
              <p className="text-xs text-slate-500">
                Retiring a plan stops it being offered. Operators already on it
                stay on it and keep being billed — nobody is moved by this.
              </p>

              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={create.isPending || update.isPending}
                  className="bg-teal-500 hover:bg-teal-400 disabled:opacity-60 text-slate-950 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
                >
                  {editing ? "Save changes" : "Create plan"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAdding(false);
                    setEditing(null);
                  }}
                  className="text-slate-400 hover:text-white text-sm font-medium transition-colors"
                >
                  Cancel
                </button>
              </div>
            </form>
          </Card>
        )}

        {isLoading ? (
          <div className="h-32 rounded-xl border border-white/10 bg-slate-900/60 animate-pulse" />
        ) : plans.length === 0 ? (
          <Card>
            <p className="text-sm text-slate-400">
              No plans yet. An operator can exist without one — they simply are
              not billed and have no caps.
            </p>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {plans.map((p) => (
              <Card key={p.id} className={p.is_active ? "" : "opacity-60"}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-semibold text-white flex items-center gap-1.5">
                      <Tag size={14} className="text-slate-500" aria-hidden="true" />
                      {p.name}
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5 font-mono">{p.slug}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-xl font-bold text-white tabular-nums">
                      {KES(p.price)}
                    </p>
                    <p className="text-xs text-slate-500">
                      every {p.billing_period_days} days
                    </p>
                  </div>
                </div>

                <div className="mt-4 pt-4 border-t border-white/10 flex items-center gap-5 text-xs text-slate-400">
                  <span>
                    {p.max_customers ? `${num(p.max_customers)} subscribers` : "Unlimited subscribers"}
                  </span>
                  <span>
                    {p.max_routers ? `${num(p.max_routers)} routers` : "Unlimited routers"}
                  </span>
                  {!p.is_active && (
                    <span className="text-slate-500 font-medium">Retired</span>
                  )}
                  {isOwner && (
                    <button
                      onClick={() => startEdit(p)}
                      className="ml-auto text-teal-400 hover:text-teal-300 font-semibold"
                    >
                      Edit
                    </button>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    </PlatformLayout>
  );
}

function handleError(e, setErrors) {
  const data = e.response?.data;
  if (data && typeof data === "object" && !data.detail) {
    setErrors(data);
    toast.error("Check the highlighted fields");
  } else {
    toast.error(data?.detail || "Couldn't save the plan");
  }
}

const inputCls = (hasError) =>
  `mt-1 w-full rounded-lg border px-3 py-2 text-sm bg-slate-950 text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 ${
    hasError ? "border-red-500/40 focus:ring-red-400" : "border-white/15 focus:ring-teal-500"
  }`;

function Field({ label, hint, error, children }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-300">{label}</span>
      {children}
      {error && <span className="text-xs text-red-300 mt-1 block">{error}</span>}
      {!error && hint && <span className="text-xs text-slate-500 mt-1 block">{hint}</span>}
    </label>
  );
}
