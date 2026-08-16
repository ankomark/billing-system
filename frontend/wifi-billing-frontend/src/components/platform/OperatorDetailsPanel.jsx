import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Building2 } from "lucide-react";
import toast from "react-hot-toast";
import { updateOperator } from "../../services/platform";

/**
 * Correct an operator's details after onboarding.
 *
 * The endpoint and its tests have existed since phase 6 and nothing ever called
 * them — a name typed wrong during onboarding could only be fixed in the
 * database. The service function was even written and left with no call site.
 *
 * Two names, because they are not the same thing and the difference is what
 * makes this safe to hand over:
 *
 *   business_name is the operator's public brand. It is what their subscribers
 *   see in every SMS and at the top of the captive portal, and it is what their
 *   own dashboard is titled. Changing it is visible to their customers.
 *
 *   name is the internal record this platform keeps them under. It orders the
 *   operator list, it is the string typed back to confirm a deletion, and
 *   Tenant.save copies it into a business_name left empty — which is why
 *   clearing the brand puts them back under this name instead of nothing.
 *
 * slug, status and public_token are not here: the serializer refuses them. The
 * slug and token are identity other things resolve against — the M-Pesa
 * callback URL and the hotspot portal both carry the token — and status has its
 * own audited endpoint, which is the toggle further down this page.
 */

const FIELDS = [
  {
    key: "business_name",
    label: "Business name",
    hint: "Their public brand — subscribers see this in every SMS and on the captive portal. Cleared, it comes back as a copy of the internal name rather than staying empty.",
    wide: true,
  },
  {
    key: "name",
    label: "Internal name",
    hint: "What this platform files them under. Required — it orders the operator list and it is the name typed back to confirm a deletion.",
    wide: true,
  },
  { key: "support_phone", label: "Support phone", hint: "e.g. 0712345678" },
  { key: "support_phone_2", label: "Second support phone" },
  { key: "contact_email", label: "Contact email", type: "email" },
  { key: "contact_phone", label: "Contact phone" },
  {
    key: "pppoe_prefix",
    label: "PPPoE prefix",
    hint: "Shapes generated usernames, e.g. NET-1234-ABC. Existing subscribers keep the usernames they already have.",
    wide: true,
  },
];

const BLANK = Object.fromEntries(FIELDS.map((f) => [f.key, ""]));

export default function OperatorDetailsPanel({ operator, canEdit }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState({});

  // `details` carries the two names apart. Older responses did not, and a form
  // filled from the collapsed display name would save the brand over the
  // internal name — so with nothing safe to edit, show nothing.
  const details = operator.details;

  useEffect(() => {
    if (details) {
      setForm({ ...BLANK, ...details });
    }
  }, [details]);

  const save = useMutation({
    mutationFn: (payload) => updateOperator(operator.id, payload),
    onSuccess: () => {
      setErrors({});
      toast.success("Saved. Their dashboard and their subscribers' messages use the new name.");
      // The heading on this page, the operator list, and the name the delete
      // dialog asks to be typed back all read the same record.
      qc.invalidateQueries({ queryKey: ["platform-operator", String(operator.id)] });
      qc.invalidateQueries({ queryKey: ["platform-operators"] });
      qc.invalidateQueries({ queryKey: ["audit-log", operator.id, 100] });
    },
    onError: (e) => {
      const data = e.response?.data;
      if (data && typeof data === "object" && !data.detail) {
        setErrors(data);
        toast.error("Check the highlighted fields");
      } else {
        toast.error(data?.detail || "Couldn't save");
      }
    },
  });

  if (!details) return null;

  const err = (k) => (Array.isArray(errors[k]) ? errors[k].join(" ") : errors[k]);

  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/80 p-5">
      <div className="mb-4">
        <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] flex items-center gap-2">
          <Building2 size={13} aria-hidden="true" />
          Operator details
        </h2>
        <p className="text-sm text-slate-400 mt-1">
          Correcting what was entered at onboarding. Every change is recorded
          against your account.
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(form);
        }}
        className="space-y-4"
      >
        <div className="grid sm:grid-cols-2 gap-4">
          {FIELDS.map((f) => (
            <label key={f.key} className={`block ${f.wide ? "sm:col-span-2" : ""}`}>
              <span className="text-sm font-medium text-slate-300">{f.label}</span>
              <input
                type={f.type || "text"}
                value={form[f.key] || ""}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                disabled={!canEdit}
                className={inputCls(err(f.key))}
              />
              {err(f.key) ? (
                <span className="mt-1 block text-xs text-red-300">{err(f.key)}</span>
              ) : (
                f.hint && (
                  <span className="mt-1 block text-xs text-slate-500">{f.hint}</span>
                )
              )}
            </label>
          ))}
        </div>

        {canEdit ? (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={save.isPending}
              className="rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-teal-400 disabled:opacity-60"
            >
              {save.isPending ? "Saving…" : "Save details"}
            </button>
            <span className="text-xs text-slate-500">
              A renamed operator sees it on their own dashboard without signing
              in again.
            </span>
          </div>
        ) : (
          <p className="text-xs text-slate-500">
            Only the platform owner can change these. The business name and
            support numbers reach this operator's subscribers.
          </p>
        )}
      </form>
    </div>
  );
}

const inputCls = (invalid) =>
  "mt-1 w-full rounded-lg border bg-slate-950 px-3 py-2 text-sm text-slate-100 " +
  "placeholder-slate-600 focus:outline-none focus:ring-2 disabled:opacity-60 " +
  (invalid
    ? "border-red-500/50 focus:ring-red-500"
    : "border-white/15 focus:ring-teal-500");
