import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Gift, X } from "lucide-react";
import toast from "react-hot-toast";
import { compAccess } from "../../services/customers";
import { fetchPackages } from "../../services/packages";

/**
 * Give a customer access without charging for it.
 *
 * For the case this exists for: somebody paid and did not get online, or was
 * let down twice, and is on the phone wanting the thing they already paid for.
 * The only ways to help before were to record a payment that never happened —
 * putting money in the books nobody received — or to do nothing.
 *
 * The reason is required, and shown as required. This is the operator writing
 * off a sale; in three months the question will be why.
 */
export default function CompAccessModal({ open, customer, onClose, onDone }) {
  const [packageId, setPackageId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);

  const { data } = useQuery({
    queryKey: ["packages", "all"],
    queryFn: () => fetchPackages(1, 100),
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });

  // A hotspot customer gets a hotspot package; a PPPoE line gets a PPPoE one.
  // Offering the whole catalogue would let someone put a home line on an hour
  // of hotspot access.
  const packages = (data?.results ?? []).filter(
    (p) =>
      !p.is_archived &&
      !!p.is_hotspot === (customer?.connection_type === "hotspot")
  );

  if (!open) return null;

  const close = () => {
    setPackageId("");
    setReason("");
    setIssued(null);
    setBusy(false);
    onClose();
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!packageId || !reason.trim()) return;
    setBusy(true);
    try {
      const res = await compAccess(customer.id, {
        packageId: Number(packageId),
        reason: reason.trim(),
      });
      setIssued(res);
      onDone?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Couldn't issue this.");
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Give access at no charge"
    >
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-slate-900 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-white/10 px-5 py-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 font-bold text-white">
              <Gift size={16} aria-hidden="true" />
              Give access, no charge
            </h2>
            <p className="mt-0.5 truncate text-sm text-slate-400">
              {customer?.full_name}
            </p>
          </div>
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
            {issued.voucher_code ? (
              <div className="rounded-xl border-2 border-emerald-500/30 bg-emerald-500/10 p-4 text-center">
                <p className="text-xs font-semibold uppercase tracking-wider text-emerald-300">
                  Access code
                </p>
                <p className="my-2 select-all font-mono text-2xl font-bold tracking-wider text-white">
                  {issued.voucher_code}
                </p>
                <p className="text-xs text-emerald-200">
                  Read this out to them. It works on the first device that uses
                  it, and only that one.
                </p>
              </div>
            ) : (
              <p className="rounded-lg bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
                {issued.detail} Their connection has been restored — no code is
                needed for a PPPoE line.
              </p>
            )}
            <p className="text-xs text-slate-500">
              Recorded as a payment of zero, so it adds nothing to your revenue
              and still shows up in your figures as something you gave away.
            </p>
            <button
              onClick={close}
              className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-700"
            >
              Done
            </button>
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
                    {p.name} · normally KES {Number(p.price).toLocaleString()}
                  </option>
                ))}
              </select>
              {packages.length === 0 && (
                <span className="mt-1 block text-xs text-amber-300">
                  No {customer?.connection_type} packages to give.
                </span>
              )}
            </label>

            <label className="block">
              <span className="text-sm font-medium text-slate-300">
                Why is this free? <span className="text-red-400">*</span>
              </span>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                placeholder="Paid on Tuesday and never got online"
                className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <span className="mt-1 block text-xs text-slate-500">
                Goes on the record against your account.
              </span>
            </label>

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={busy || !packageId || !reason.trim()}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
              >
                {busy ? "Issuing…" : "Give it to them"}
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
