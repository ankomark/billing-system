import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Ticket, X } from "lucide-react";
import toast from "react-hot-toast";
import { fetchPackages } from "../../services/packages";
import { issueVoucher } from "../../services/customers";
import { getUser } from "../../services/auth";
import { isOperatorAdmin } from "../../constants/roles";
import { Card } from "./ui";

/**
 * Selling a voucher over the counter.
 *
 * The captive portal's flow, run by the operator instead of the customer:
 * pick a package, take their number, say how it was paid, hand over the code.
 * No M-Pesa prompt and no waiting for a callback — the money has already
 * changed hands, or is being waived.
 *
 * First thing on the dashboard because it is the transaction, not a report
 * about transactions.
 */
export default function IssueVoucherCard() {
  const [open, setOpen] = useState(false);
  if (!isOperatorAdmin(getUser()?.role)) return null;

  return (
    <>
      <IssueVoucherModal open={open} onClose={() => setOpen(false)} />
      {/* Azure arriving from the right, where the button sits. This card is
          an action, not a readout — see CARD_SHEEN. */}
      <Card sheen="azure">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setOpen(true)}
            aria-label="Issue a voucher"
            className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white shadow-lg shadow-blue-600/25 transition-colors hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-2 focus:ring-offset-slate-900"
          >
            <Plus size={22} strokeWidth={2.5} />
          </button>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white">Issue a voucher</p>
            <p className="mt-0.5 text-xs text-slate-400">
              Sell one at the counter, or give it away
            </p>
          </div>
        </div>
      </Card>
    </>
  );
}

function IssueVoucherModal({ open, onClose }) {
  const [packageId, setPackageId] = useState("");
  const [phone, setPhone] = useState("");
  const [paidWith, setPaidWith] = useState("cash");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);

  const { data } = useQuery({
    queryKey: ["packages", "issue"],
    queryFn: () => fetchPackages(1, 100),
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });
  const packages = (data?.results ?? []).filter((p) => p.is_hotspot && !p.is_archived);

  if (!open) return null;

  const free = paidWith === "comp";
  const ready = packageId && phone.trim() && (!free || reason.trim());

  const reset = () => {
    setPackageId("");
    setPhone("");
    setPaidWith("cash");
    setReason("");
    setIssued(null);
    setBusy(false);
  };

  const close = () => {
    reset();
    onClose();
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    try {
      setIssued(
        await issueVoucher({
          packageId: Number(packageId),
          phone: phone.trim(),
          paidWith,
          reason: reason.trim(),
        })
      );
    } catch (err) {
      toast.error(err.response?.data?.detail || "Couldn't issue that.");
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Issue a voucher"
    >
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-slate-900 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-white/10 px-5 py-4">
          <h2 className="flex items-center gap-2 font-bold text-white">
            <Ticket size={16} aria-hidden="true" />
            Issue a voucher
          </h2>
          <button
            onClick={close}
            disabled={busy && !issued}
            className="text-slate-500 transition-colors hover:text-white disabled:opacity-40"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {issued ? (
          <div className="space-y-4 px-5 py-4">
            <div className="rounded-xl border-2 border-emerald-500/30 bg-emerald-500/10 p-4 text-center">
              <p className="text-xs font-semibold uppercase tracking-wider text-emerald-300">
                {issued.free ? "Free access code" : "Access code"}
              </p>
              <p className="my-2 select-all font-mono text-3xl font-bold tracking-wider text-white">
                {issued.voucher_code}
              </p>
              <p className="text-xs text-emerald-200">
                Read it out to them. It works on the first device that uses it,
                and only that one.
              </p>
            </div>

            <p className="text-xs text-slate-500">
              {issued.new_customer ? "New subscriber " : "Existing subscriber "}
              <span className="text-slate-400">{issued.customer_name}</span>
              {issued.free && " · recorded at no charge, so your revenue is unchanged"}
            </p>

            <div className="flex gap-3">
              <button
                onClick={reset}
                className="flex-1 rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-700"
              >
                Issue another
              </button>
              <button
                onClick={close}
                className="flex-1 rounded-lg border border-white/15 py-2.5 text-sm font-semibold text-slate-200 transition-colors hover:bg-white/5"
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4 px-5 py-4">
            <label className="block">
              <span className="text-sm font-medium text-slate-300">Package</span>
              <select
                value={packageId}
                onChange={(e) => setPackageId(e.target.value)}
                required
                className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Choose…</option>
                {packages.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · KES {Number(p.price).toLocaleString()}
                  </option>
                ))}
              </select>
              {packages.length === 0 && (
                <span className="mt-1 block text-xs text-amber-300">
                  No hotspot packages yet — add one first.
                </span>
              )}
            </label>

            <label className="block">
              <span className="text-sm font-medium text-slate-300">
                Customer's number
              </span>
              <input
                type="tel"
                inputMode="numeric"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                required
                placeholder="07XX XXX XXX"
                className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <span className="mt-1 block text-xs text-slate-500">
                A number that has bought before is reused, not duplicated.
              </span>
            </label>

            <label className="block">
              <span className="text-sm font-medium text-slate-300">Paid with</span>
              <select
                value={paidWith}
                onChange={(e) => setPaidWith(e.target.value)}
                className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="cash">Cash</option>
                <option value="mpesa">M-Pesa</option>
                <option value="bank">Bank</option>
                <option value="comp">Free — no charge</option>
              </select>
            </label>

            {free && (
              <label className="block">
                <span className="text-sm font-medium text-slate-300">
                  Why is this free? <span className="text-red-400">*</span>
                </span>
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  required
                  placeholder="Router was down all morning"
                  className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <span className="mt-1 block text-xs text-slate-500">
                  Goes on the record against your account. Your revenue is not
                  affected, and the giveaway stays countable.
                </span>
              </label>
            )}

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={busy || !ready}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
              >
                {busy ? "Issuing…" : free ? "Give it free" : "Issue voucher"}
              </button>
              <button
                type="button"
                onClick={close}
                className="text-sm font-medium text-slate-400 transition-colors hover:text-white"
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
