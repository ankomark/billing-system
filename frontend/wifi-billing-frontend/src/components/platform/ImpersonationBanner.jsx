import { Eye, X } from "lucide-react";
import { getImpersonation, stopImpersonating } from "../../services/auth";

/**
 * Shown whenever platform staff are viewing as an operator.
 *
 * Deliberately loud and always present. The failure this prevents is someone
 * forgetting they are impersonating and reading — or worse, changing — a real
 * operator's records believing they are their own. Every request made in this
 * state is recorded against them by name.
 */
export default function ImpersonationBanner() {
  const active = getImpersonation();

  if (!active) return null;

  const exit = () => {
    stopImpersonating();
    // Full reload: cached queries were fetched as the operator and would
    // otherwise linger after switching back.
    window.location.href = "/platform/operators";
  };

  return (
    <div className="bg-amber-500 text-amber-950 px-4 py-2.5 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Eye size={16} className="flex-shrink-0" />
        <span>
          Viewing as <strong>{active.name}</strong>. Everything you do here is
          recorded against your account.
        </span>
      </div>
      <button
        onClick={exit}
        className="inline-flex items-center gap-1.5 bg-amber-950/10 hover:bg-amber-950/20 px-3 py-1 rounded-md text-sm font-semibold transition-colors flex-shrink-0"
      >
        <X size={14} />
        Stop
      </button>
    </div>
  );
}
