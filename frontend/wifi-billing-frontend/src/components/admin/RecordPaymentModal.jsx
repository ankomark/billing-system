import { useMemo, useState } from "react";
import { Banknote, X } from "lucide-react";
import toast from "react-hot-toast";
import { recordPayment } from "../../services/customers";

const METHODS = [
  ["cash", "Cash"],
  ["mpesa", "M-Pesa"],
  ["bank", "Bank"],
];

/**
 * Take money for a bill that already exists.
 *
 * A hotspot walk-up pays through the portal and the callback settles their
 * invoice. A PPPoE line is the other way round — created first, paid
 * afterwards — and there was nowhere to record that. The choices were "give
 * free access", which writes the sale off, or making the customer again with a
 * package, which duplicates them.
 *
 * Deliberately not called "mark as paid". Recording the payment is what
 * settles the invoice, activates the subscription and puts the account on the
 * router; a flag flipped on its own would tidy the books and leave the
 * customer refused by the hardware.
 */
export default function RecordPaymentModal({ open, customer, onClose, onDone }) {
  const [subscriptionId, setSubscriptionId] = useState("");
  const [method, setMethod] = useState("cash");
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);

  // Only what can actually be settled. A paid subscription in the list is an
  // invitation to take the same money twice, which the server refuses anyway —
  // better not to offer it.
  const outstanding = useMemo(
    () =>
      (customer?.subscriptions ?? []).filter(
        (s) => s.payment_status === "unpaid" || s.payment_status === "pending"
      ),
    [customer]
  );

  const chosen =
    outstanding.find((s) => String(s.id) === String(subscriptionId)) ||
    outstanding[0];

  if (!open) return null;

  const close = () => {
    setSubscriptionId("");
    setMethod("cash");
    setAmount("");
    setReference("");
    setBusy(false);
    onClose();
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!chosen) return;
    setBusy(true);
    try {
      const res = await recordPayment(customer.id, {
        subscriptionId: chosen.id,
        method,
        amount,
        reference: reference.trim(),
      });
      toast.success(res.detail || "Payment recorded.");
      onDone?.();
      close();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Couldn't record that.");
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
          <div className="flex items-center gap-2">
            <Banknote size={18} className="text-emerald-300" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-white">Record a payment</h2>
          </div>
          <button
            onClick={close}
            aria-label="Close"
            className="rounded-lg p-1 text-slate-400 transition-colors hover:bg-white/5 hover:text-white"
          >
            <X size={18} />
          </button>
        </div>

        {!outstanding.length ? (
          <div className="px-5 py-6">
            <p className="text-sm text-slate-300">
              {customer?.full_name} has nothing outstanding.
            </p>
            <p className="mt-2 text-xs text-slate-500">
              Every subscription on this account is already settled. To sell
              them something new, use Issue a voucher or add a package.
            </p>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4 px-5 py-5">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-slate-300">
                What is being paid for
              </label>
              <select
                value={String(chosen?.id ?? "")}
                onChange={(e) => setSubscriptionId(e.target.value)}
                className="w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-emerald-500"
              >
                {outstanding.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.package_name}
                    {s.amount_due ? ` — ${s.amount_due}` : ""}
                    {s.invoice_number ? ` (${s.invoice_number})` : ""}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-slate-300">
                How it was paid
              </label>
              <div className="flex gap-2">
                {METHODS.map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setMethod(value)}
                    className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                      method === value
                        ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-200"
                        : "border-white/15 text-slate-300 hover:bg-white/5"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-slate-300">
                Amount
              </label>
              <input
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                inputMode="decimal"
                placeholder={chosen?.amount_due ?? "the full bill"}
                className="w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
              {/* Left blank on purpose most of the time: the bill is what they
                  were asked for, and typing it again is a chance to get it
                  wrong. Filled in only when less was taken. */}
              <p className="mt-1.5 text-xs text-slate-500">
                Leave blank for the full bill. Enter a figure only if they paid
                a different amount.
              </p>
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-slate-300">
                Reference <span className="text-slate-500">(optional)</span>
              </label>
              <input
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="M-Pesa code, receipt number…"
                className="w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={close}
                className="rounded-lg px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-white/5"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy ? "Recording…" : "Record payment"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
