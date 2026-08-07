import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Building2 } from "lucide-react";
import toast from "react-hot-toast";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { createOperator, fetchPlatformPlans } from "../../services/platform";

/**
 * Onboard an operator.
 *
 * The login is part of this form rather than a later step, because an operator
 * with no admin account cannot be reached by anyone — the backend creates both
 * in one transaction for the same reason.
 */
export default function NewOperator() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [form, setForm] = useState({
    name: "",
    business_name: "",
    support_phone: "",
    pppoe_prefix: "",
    contact_email: "",
    contact_phone: "",
    admin_username: "",
    admin_password: "",
    admin_email: "",
    plan: "",
    mpesa_env: "sandbox",
    mpesa_consumer_key: "",
    mpesa_consumer_secret: "",
    mpesa_shortcode: "",
    mpesa_shortcode_type: "paybill",
    mpesa_store_number: "",
    mpesa_passkey: "",
  });
  // Field-level errors as returned by the serializer, keyed by field name.
  const [errors, setErrors] = useState({});

  const { data: plans = [] } = useQuery({
    queryKey: ["platform-plans"],
    queryFn: fetchPlatformPlans,
    staleTime: 5 * 60 * 1000,
  });

  const set = (k) => (e) => {
    setForm((f) => ({ ...f, [k]: e.target.value }));
    setErrors((prev) => (prev[k] ? { ...prev, [k]: undefined } : prev));
  };

  const mutation = useMutation({
    mutationFn: createOperator,
    onSuccess: (op) => {
      qc.invalidateQueries({ queryKey: ["platform-operators"] });
      qc.invalidateQueries({ queryKey: ["platform-overview"] });
      qc.invalidateQueries({ queryKey: ["platform-health"] });
      toast.success(
        op.payments_missing?.length
          ? `${op.name} created — finish M-Pesa setup so they can be paid`
          : `${op.name} created and ready to take payments`
      );
      navigate(`/platform/operators/${op.id}`);
    },
    onError: (e) => {
      const data = e.response?.data;
      if (data && typeof data === "object" && !data.detail) {
        setErrors(data);
        toast.error("Check the highlighted fields");
      } else {
        toast.error(data?.detail || "Couldn't create the operator");
      }
    },
  });

  const submit = (e) => {
    e.preventDefault();
    if (!form.name.trim()) return toast.error("Give the operator a name");
    if (!form.admin_username.trim()) return toast.error("The admin needs a username");
    if (form.admin_password.length < 8)
      return toast.error("Admin password must be at least 8 characters");

    // Send only what was filled in — the serializer treats absent optional
    // fields differently from empty ones for the slug and prefix defaults.
    const payload = Object.fromEntries(
      Object.entries(form).filter(([, v]) => String(v).trim() !== "")
    );
    mutation.mutate(payload);
  };

  return (
    <PlatformLayout>
      <form onSubmit={submit} className="space-y-6 max-w-2xl">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate("/platform/operators")}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Back to operators"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-white">New operator</h1>
            <p className="text-slate-400 text-sm mt-0.5">
              Creates the business and its first admin login
            </p>
          </div>
        </div>

        <Section title="The business">
          <Field label="Operator name" required error={errors.name}>
            <input
              value={form.name}
              onChange={set("name")}
              placeholder="Coastal Fibre Ltd"
              className={input(errors.name)}
            />
          </Field>
          <Field
            label="Business name"
            hint="Shown to their subscribers in SMS. Defaults to the operator name."
            error={errors.business_name}
          >
            <input
              value={form.business_name}
              onChange={set("business_name")}
              placeholder="Coastal Fibre"
              className={input(errors.business_name)}
            />
          </Field>
          <Field label="Support phone" error={errors.support_phone}>
            <input
              value={form.support_phone}
              onChange={set("support_phone")}
              placeholder="0722334455"
              className={input(errors.support_phone)}
            />
          </Field>
          <Field
            label="PPPoE prefix"
            hint="Prefix for generated usernames, e.g. CFL-1234-AB. Defaults to NET."
            error={errors.pppoe_prefix}
          >
            <input
              value={form.pppoe_prefix}
              onChange={set("pppoe_prefix")}
              placeholder="CFL"
              maxLength={10}
              className={input(errors.pppoe_prefix)}
            />
          </Field>
          <Field label="Contact email" error={errors.contact_email}>
            <input
              type="email"
              value={form.contact_email}
              onChange={set("contact_email")}
              placeholder="ops@coastalfibre.co.ke"
              className={input(errors.contact_email)}
            />
          </Field>
          <Field label="Contact phone" error={errors.contact_phone}>
            <input
              value={form.contact_phone}
              onChange={set("contact_phone")}
              placeholder="0722334455"
              className={input(errors.contact_phone)}
            />
          </Field>
        </Section>

        <Section title="Their first admin">
          <Field label="Username" required error={errors.admin_username}>
            <input
              value={form.admin_username}
              onChange={set("admin_username")}
              autoComplete="off"
              placeholder="coastadmin"
              className={input(errors.admin_username)}
            />
          </Field>
          <Field
            label="Password"
            required
            hint="At least 8 characters. Pass it to them directly — it is not emailed."
            error={errors.admin_password}
          >
            <input
              type="password"
              value={form.admin_password}
              onChange={set("admin_password")}
              autoComplete="new-password"
              className={input(errors.admin_password)}
            />
          </Field>
          <Field label="Admin email" error={errors.admin_email}>
            <input
              type="email"
              value={form.admin_email}
              onChange={set("admin_email")}
              className={input(errors.admin_email)}
            />
          </Field>
        </Section>

        <Section title="Taking payments">
          <div className="sm:col-span-2 -mb-1">
            <p className="text-xs text-slate-400">
              Optional, and usually not available yet — a new operator is
              normally still waiting on Safaricom for their own Daraja app.
              Leave this blank and finish it from their page later. Until it is
              done they can sign in and set everything up, but nobody can pay
              them.
            </p>
          </div>
          <Field label="Environment" error={errors.mpesa_env}>
            <select value={form.mpesa_env} onChange={set("mpesa_env")} className={input(errors.mpesa_env)}>
              <option value="sandbox">Sandbox — for testing</option>
              <option value="production">Production — real money</option>
            </select>
          </Field>
          {/* Asked here because the number does not say which it is, and
              nothing the platform can see reveals the answer later. A till
              pushed as a PayBill is accepted by Safaricom and then fails with
              a code that names nothing, having sent no prompt. */}
          <Field label="How they get paid" error={errors.mpesa_shortcode_type}>
            <select value={form.mpesa_shortcode_type}
                    onChange={set("mpesa_shortcode_type")}
                    className={input(errors.mpesa_shortcode_type)}>
              <option value="paybill">PayBill</option>
              <option value="till">Buy Goods (till)</option>
            </select>
          </Field>

          <Field label="Shortcode / till" hint="e.g. 600000" error={errors.mpesa_shortcode}>
            <input value={form.mpesa_shortcode} onChange={set("mpesa_shortcode")}
                   className={input(errors.mpesa_shortcode)} />
          </Field>

          {form.mpesa_shortcode_type === "till" && (
            <Field label="Store number"
                   hint="Buy Goods only — leave blank if it is the same as the till"
                   error={errors.mpesa_store_number}>
              <input value={form.mpesa_store_number}
                     onChange={set("mpesa_store_number")}
                     className={input(errors.mpesa_store_number)} />
            </Field>
          )}
          <Field label="Consumer key" error={errors.mpesa_consumer_key}>
            <input value={form.mpesa_consumer_key} onChange={set("mpesa_consumer_key")}
                   autoComplete="off" className={input(errors.mpesa_consumer_key)} />
          </Field>
          <Field label="Consumer secret" error={errors.mpesa_consumer_secret}>
            <input type="password" value={form.mpesa_consumer_secret}
                   onChange={set("mpesa_consumer_secret")} autoComplete="new-password"
                   className={input(errors.mpesa_consumer_secret)} />
          </Field>
          <Field label="Passkey" error={errors.mpesa_passkey}>
            <input type="password" value={form.mpesa_passkey} onChange={set("mpesa_passkey")}
                   autoComplete="new-password" className={input(errors.mpesa_passkey)} />
          </Field>
        </Section>

        <Section title="Plan">
          <Field
            label="Platform plan"
            hint="Optional. An operator can start with no plan and be put on one later."
            error={errors.plan}
          >
            <select value={form.plan} onChange={set("plan")} className={input(errors.plan)}>
              <option value="">No plan yet</option>
              {plans.map((p) => (
                <option key={p.id} value={p.slug}>
                  {p.name} — KES {Number(p.price).toLocaleString()} /{" "}
                  {p.billing_period_days} days
                </option>
              ))}
            </select>
          </Field>
        </Section>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="inline-flex items-center gap-2 bg-teal-500 hover:bg-teal-400 disabled:opacity-60 text-slate-950 rounded-lg px-5 py-2.5 text-sm font-semibold transition-colors"
          >
            <Building2 size={16} />
            {mutation.isPending ? "Creating…" : "Create operator"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/platform/operators")}
            className="text-slate-400 hover:text-white text-sm font-medium transition-colors"
          >
            Cancel
          </button>
        </div>
      </form>
    </PlatformLayout>
  );
}

const input = (hasError) =>
  `w-full rounded-lg border px-3 py-2 text-sm bg-slate-950 text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 ${
    hasError
      ? "border-red-500/40 focus:ring-red-400"
      : "border-white/15 focus:ring-teal-500"
  }`;

function Section({ title, children }) {
  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/80 p-5">
      <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] mb-4">
        {title}
      </p>
      <div className="grid sm:grid-cols-2 gap-4">{children}</div>
    </div>
  );
}

function Field({ label, hint, required, error, children }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-300">
        {label}
        {required && <span className="text-red-500"> *</span>}
      </span>
      <div className="mt-1">{children}</div>
      {error && (
        <span className="text-xs text-red-300 mt-1 block">
          {Array.isArray(error) ? error.join(" ") : String(error)}
        </span>
      )}
      {!error && hint && (
        <span className="text-xs text-slate-500 mt-1 block">{hint}</span>
      )}
    </label>
  );
}
