/**
 * DARK CONSOLE ONLY.
 *
 * This folder is named as though it were theme-neutral and is not: these were
 * darkened along with the operator console, and every page that imports them —
 * operator and platform alike — is dark.
 *
 * A light page using one of these will render invisibly, which is not a
 * hypothetical: the app-level loading screen did exactly that until it was
 * given its own neutral bars. If you need one of these on a light surface,
 * give it a variant rather than assuming it adapts.
 */
const map = {
  active:    "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  expired:   "bg-red-500/10 text-red-300 border-red-500/30",
  suspended: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  paid:      "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  unpaid:    "bg-red-500/10 text-red-300 border-red-500/30",
  pending:   "bg-amber-500/10 text-amber-300 border-amber-500/30",
  online:    "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  offline:   "bg-red-500/10 text-red-300 border-red-500/30",
  success:   "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  failed:    "bg-red-500/10 text-red-300 border-red-500/30",
  pppoe:     "bg-blue-500/10 text-blue-300 border-blue-500/30",
  hotspot:   "bg-violet-50 text-violet-300 border-violet-200",
};

export default function StatusBadge({ status, className = "" }) {
  const cls = map[status] || "bg-white/5 text-slate-300 border-white/10";
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border capitalize ${cls} ${className}`}
    >
      {status}
    </span>
  );
}
