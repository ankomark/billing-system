import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Building2, X } from "lucide-react";
import toast from "react-hot-toast";
import { fetchOperators } from "../../services/platform";
import { startImpersonating } from "../../services/auth";

/**
 * Choose an operator, then open their dashboard as they see it.
 *
 * This is impersonation, not a shortcut — the same flow OperatorDetail uses.
 * The reason is required rather than optional because every request made while
 * impersonating is written to ImpersonationLog, and a log of "someone looked at
 * this operator's subscribers" is worth much less without why.
 */
export default function OperatorPickerModal({ open, onClose }) {
  const navigate = useNavigate();
  const [selected, setSelected] = useState(null);
  const [reason, setReason] = useState("");

  const { data: operators = [], isLoading } = useQuery({
    queryKey: ["platform-operators", ""],
    queryFn: () => fetchOperators(undefined),
    staleTime: 30 * 1000,
    enabled: open,
  });

  if (!open) return null;

  const go = () => {
    if (!selected) {
      toast.error("Pick an operator first");
      return;
    }
    if (!reason.trim()) {
      toast.error("Say why you're opening this account — it goes in the audit log.");
      return;
    }
    startImpersonating({ id: selected.id, name: selected.name, reason: reason.trim() });
    navigate("/admin/dashboard");
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-900/60 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Open an operator dashboard"
    >
      <div className="bg-white rounded-xl w-full max-w-lg max-h-[85vh] flex flex-col shadow-xl">
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-slate-200">
          <div>
            <h2 className="font-bold text-slate-800">Open an operator dashboard</h2>
            <p className="text-slate-500 text-sm mt-0.5">
              You'll see exactly what they see. Every request is logged.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-auto px-5 py-3">
          {isLoading ? (
            <p className="text-slate-500 text-sm py-6 text-center">Loading operators…</p>
          ) : operators.length === 0 ? (
            <p className="text-slate-500 text-sm py-6 text-center">
              No operators yet. Create one first.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {operators.map((op) => {
                const active = selected?.id === op.id;
                return (
                  <li key={op.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(op)}
                      aria-pressed={active}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left transition-colors ${
                        active
                          ? "border-teal-500 bg-teal-50"
                          : "border-slate-200 hover:bg-slate-50"
                      }`}
                    >
                      <Building2 size={16} className="text-slate-400 flex-shrink-0" />
                      <span className="flex-1 min-w-0">
                        <span className="block text-sm font-medium text-slate-800 truncate">
                          {op.name}
                        </span>
                        <span className="block text-xs text-slate-500 capitalize">
                          {op.status.replace("_", " ")} · {op.subscribers} subscriber
                          {op.subscribers === 1 ? "" : "s"}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="px-5 py-4 border-t border-slate-200 space-y-3">
          <label className="block">
            <span className="text-sm font-medium text-slate-700">
              Why are you opening this account?
            </span>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Support ticket 412 — customer reports no connection"
              className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
            />
          </label>
          <div className="flex items-center gap-3">
            <button
              onClick={go}
              className="bg-teal-600 hover:bg-teal-700 text-white rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
            >
              Open dashboard
            </button>
            <button
              onClick={onClose}
              className="text-slate-500 hover:text-slate-800 text-sm font-medium"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
