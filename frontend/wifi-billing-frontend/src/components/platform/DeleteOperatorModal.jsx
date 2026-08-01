import { useState } from "react";
import { Trash2, TriangleAlert, X } from "lucide-react";
import toast from "react-hot-toast";
import { deleteOperator } from "../../services/platform";

/**
 * Erase a closed operator.
 *
 * The name has to be typed back. That is not ceremony — it is the only thing
 * standing between a mis-click and destroyed invoices and payment history, and
 * unlike every other action on this page there is nothing to restore from
 * afterwards.
 *
 * What it removes is spelled out before, not after. Someone who reads "delete
 * permanently" is usually thinking about the login, not about the two years of
 * M-Pesa receipts that go with it.
 */
export default function DeleteOperatorModal({ open, operator, onClose, onDeleted }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  // The backend compares against business_name or name; the detail endpoint
  // already returns exactly that as `name`.
  const expected = operator?.name || "";
  const matches = typed.trim() === expected;

  const close = () => {
    setTyped("");
    onClose();
  };

  const submit = async () => {
    if (!matches) return;
    setBusy(true);
    try {
      const res = await deleteOperator(operator.id, typed.trim());
      toast.success(res.detail || "Operator deleted");
      onDeleted?.(res);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Couldn't delete this operator");
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Delete operator permanently"
    >
      <div className="w-full max-w-md rounded-xl border border-red-500/25 bg-slate-900 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-white/10 px-5 py-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 font-bold text-white">
              <Trash2 size={16} aria-hidden="true" />
              Delete permanently
            </h2>
            <p className="mt-0.5 truncate text-sm text-slate-400">{expected}</p>
          </div>
          <button
            onClick={close}
            disabled={busy}
            className="text-slate-500 transition-colors hover:text-white disabled:opacity-40"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <div className="flex gap-3 rounded-lg border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            <TriangleAlert size={16} className="mt-0.5 flex-shrink-0" aria-hidden="true" />
            <p>This cannot be undone. There is no backup to restore from here.</p>
          </div>

          <div>
            <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-slate-500">
              What goes with them
            </p>
            <ul className="space-y-1 text-sm text-slate-400">
              <li>Their subscribers, packages and subscriptions</li>
              <li>Every invoice and payment, theirs and the platform's</li>
              <li>Their routers, stations and all network history</li>
              <li>Their staff logins</li>
            </ul>
            <p className="mt-2 text-xs text-slate-500">
              A record that you deleted them, and what was destroyed, stays in
              the audit log.
            </p>
          </div>

          <label className="block">
            <span className="text-sm font-medium text-slate-300">
              Type <span className="font-mono text-slate-100">{expected}</span> to confirm
            </span>
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              disabled={busy}
              className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-red-500"
            />
          </label>

          <div className="flex items-center gap-3">
            <button
              onClick={submit}
              disabled={busy || !matches}
              className="rounded-lg bg-red-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-red-400 disabled:opacity-40"
            >
              {busy ? "Deleting…" : "Delete permanently"}
            </button>
            <button
              onClick={close}
              disabled={busy}
              className="text-sm font-medium text-slate-400 transition-colors hover:text-white disabled:opacity-40"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
