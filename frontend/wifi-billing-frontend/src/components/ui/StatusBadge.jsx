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
