const map = {
  active:    "bg-emerald-50 text-emerald-700 border-emerald-200",
  expired:   "bg-red-50 text-red-700 border-red-200",
  suspended: "bg-amber-50 text-amber-700 border-amber-200",
  paid:      "bg-emerald-50 text-emerald-700 border-emerald-200",
  unpaid:    "bg-red-50 text-red-700 border-red-200",
  pending:   "bg-amber-50 text-amber-700 border-amber-200",
  online:    "bg-emerald-50 text-emerald-700 border-emerald-200",
  offline:   "bg-red-50 text-red-700 border-red-200",
  success:   "bg-emerald-50 text-emerald-700 border-emerald-200",
  failed:    "bg-red-50 text-red-700 border-red-200",
  pppoe:     "bg-blue-50 text-blue-700 border-blue-200",
  hotspot:   "bg-violet-50 text-violet-700 border-violet-200",
};

export default function StatusBadge({ status, className = "" }) {
  const cls = map[status] || "bg-slate-100 text-slate-600 border-slate-200";
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border capitalize ${cls} ${className}`}
    >
      {status}
    </span>
  );
}
