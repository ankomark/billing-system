import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, CheckCircle2, Copy, Wallet } from "lucide-react";
import toast from "react-hot-toast";
import {
  fetchOperatorMpesa,
  testOperatorMpesa,
  updateOperatorMpesa,
} from "../../services/platform";

/**
 * Finish an operator's payment setup for them.
 *
 * An operator waiting on Safaricom looks completely healthy from their own
 * dashboard — nothing errors, there is simply no revenue — so this is the one
 * problem worth putting in front of whoever is helping them. The callback URL
 * is here too, because it is the value Safaricom asks for and the operator has
 * no other way to find it.
 *
 * Secrets already stored read back as "********". Sending that value again
 * leaves them untouched, so editing the shortcode does not wipe a passkey
 * nobody retyped.
 */

const FIELDS = [
  { key: "MPESA_CONSUMER_KEY", label: "Consumer key" },
  { key: "MPESA_CONSUMER_SECRET", label: "Consumer secret", secret: true },
  { key: "MPESA_SHORTCODE", label: "Shortcode / till", hint: "e.g. 600000" },
  { key: "MPESA_PASSKEY", label: "Passkey", secret: true },
];

export default function MpesaSetupPanel({ operatorId, canEdit }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({});
  const [copied, setCopied] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["operator-mpesa", operatorId],
    queryFn: () => fetchOperatorMpesa(operatorId),
  });

  useEffect(() => {
    if (data) {
      setForm({
        MPESA_ENV: data.MPESA_ENV || "sandbox",
        MPESA_CONSUMER_KEY: data.MPESA_CONSUMER_KEY || "",
        MPESA_CONSUMER_SECRET: data.MPESA_CONSUMER_SECRET || "",
        MPESA_SHORTCODE: data.MPESA_SHORTCODE || "",
        MPESA_PASSKEY: data.MPESA_PASSKEY || "",
      });
    }
  }, [data]);

  const save = useMutation({
    mutationFn: (payload) => updateOperatorMpesa(operatorId, payload),
    onSuccess: (res) => {
      toast.success(
        res.missing?.length
          ? `Saved — still missing ${res.missing.join(", ")}`
          : "Saved. This operator can be paid."
      );
      qc.invalidateQueries({ queryKey: ["operator-mpesa", operatorId] });
      qc.invalidateQueries({ queryKey: ["platform-operator", String(operatorId)] });
      qc.invalidateQueries({ queryKey: ["platform-health"] });
    },
    onError: (e) => toast.error(e.response?.data?.detail || "Couldn't save"),
  });

  const test = useMutation({
    mutationFn: () => testOperatorMpesa(operatorId),
    onSuccess: (res) => toast.success(res.detail || "Credentials accepted"),
    onError: (e) =>
      // Safaricom's own message, which says far more than anything we could
      // guess from the shape of the values.
      toast.error(e.response?.data?.error || "Safaricom rejected these details"),
  });

  const copyCallback = async () => {
    try {
      await navigator.clipboard.writeText(data.callback_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Couldn't copy — select it manually.");
    }
  };

  if (isLoading) {
    return (
      <div className="rounded-xl border border-white/10 bg-slate-900/80 p-5">
        <div className="h-32 animate-pulse rounded-lg bg-white/5" />
      </div>
    );
  }

  const configured = data?.configured;

  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/80 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] flex items-center gap-2">
            <Wallet size={13} aria-hidden="true" />
            Taking payments
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            Their own Daraja credentials. Money goes to their till, never ours.
          </p>
        </div>
        {/* Icon as well as colour — status is never carried by colour alone. */}
        {configured ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-300">
            <CheckCircle2 size={12} aria-hidden="true" /> Set up
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-0.5 text-xs font-medium text-amber-300">
            <AlertTriangle size={12} aria-hidden="true" /> Cannot be paid yet
          </span>
        )}
      </div>

      {!configured && (
        <p className="mb-4 rounded-lg border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          Still missing {data.missing.join(", ")}. Nothing looks broken from
          their side — subscribers just cannot pay them.
        </p>
      )}

      {data.callback_url && (
        <div className="mb-5">
          <p className="text-xs text-slate-400 mb-1">
            Callback URL — paste this into their Daraja app
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate rounded-lg border border-white/10 bg-slate-950 px-3 py-2 font-mono text-xs text-teal-300">
              {data.callback_url}
            </code>
            <button
              onClick={copyCallback}
              className="rounded-lg border border-white/15 px-3 py-2 text-slate-300 hover:bg-white/5 transition-colors"
              aria-label="Copy callback URL"
            >
              {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
            </button>
          </div>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(form);
        }}
        className="space-y-4"
      >
        <div className="grid sm:grid-cols-2 gap-4">
          <label className="block">
            <span className="text-sm font-medium text-slate-300">Environment</span>
            <select
              value={form.MPESA_ENV || "sandbox"}
              onChange={(e) => setForm({ ...form, MPESA_ENV: e.target.value })}
              disabled={!canEdit}
              className={inputCls}
            >
              <option value="sandbox">Sandbox — for testing</option>
              <option value="production">Production — real money</option>
            </select>
          </label>

          {FIELDS.map((f) => (
            <label key={f.key} className="block">
              <span className="text-sm font-medium text-slate-300">{f.label}</span>
              <input
                type={f.secret ? "password" : "text"}
                value={form[f.key] || ""}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                placeholder={f.hint}
                autoComplete="off"
                disabled={!canEdit}
                className={inputCls}
              />
              {f.secret && data[f.key] === "********" && (
                <span className="mt-1 block text-xs text-slate-500">
                  Already set. Leave as-is to keep it.
                </span>
              )}
            </label>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {canEdit && (
            <button
              type="submit"
              disabled={save.isPending}
              className="rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-teal-400 disabled:opacity-60"
            >
              {save.isPending ? "Saving…" : "Save credentials"}
            </button>
          )}
          <button
            type="button"
            onClick={() => test.mutate()}
            disabled={test.isPending}
            className="rounded-lg border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition-colors hover:bg-white/5 disabled:opacity-60"
          >
            {test.isPending ? "Asking Safaricom…" : "Test connection"}
          </button>
          <span className="text-xs text-slate-500">
            The test asks Safaricom for a token with these details — it either
            authenticates or it does not.
          </span>
        </div>
      </form>
    </div>
  );
}

const inputCls =
  "mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm " +
  "text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 " +
  "focus:ring-teal-500 disabled:opacity-60";
