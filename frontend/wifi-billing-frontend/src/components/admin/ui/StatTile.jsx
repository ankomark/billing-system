import { AlertTriangle, CheckCircle2, AlertOctagon, Info } from "lucide-react";

/**
 * One number and its label.
 *
 * The right form when there is a single value to read and no shape to see — a
 * chart of one number is a decorated number.
 *
 * `tone` is a state, not a palette slot: it uses the reserved status colours,
 * which never double as series colours, and always pairs one with an icon.
 */

const TONES = {
  neutral: { ring: "border-slate-200", value: "text-slate-900", icon: null },
  good: { ring: "border-emerald-200", value: "text-emerald-700", icon: CheckCircle2 },
  warning: { ring: "border-amber-200", value: "text-amber-700", icon: AlertTriangle },
  serious: { ring: "border-orange-200", value: "text-orange-700", icon: AlertOctagon },
  critical: { ring: "border-red-200", value: "text-red-700", icon: AlertOctagon },
  info: { ring: "border-blue-200", value: "text-blue-700", icon: Info },
};

export default function StatTile({ label, value, sub, tone = "neutral", onClick, title }) {
  const t = TONES[tone] || TONES.neutral;
  const Icon = t.icon;
  const Tag = onClick ? "button" : "div";

  return (
    <Tag
      onClick={onClick}
      title={title}
      className={`w-full text-left rounded-xl border bg-white p-4 shadow-sm transition-colors ${t.ring} ${
        onClick ? "hover:bg-slate-50 cursor-pointer" : ""
      }`}
    >
      <div className="flex items-center gap-1.5">
        {Icon && <Icon size={13} className={t.value} aria-hidden="true" />}
        <p className="text-xs font-medium text-slate-500">{label}</p>
      </div>
      {/* Proportional figures for a standalone number; tabular is for columns
          that must align down a table. */}
      <p className={`text-2xl font-bold mt-1.5 ${t.value}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </Tag>
  );
}
