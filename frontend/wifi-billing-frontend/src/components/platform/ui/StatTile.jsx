import { AlertTriangle, CheckCircle2, AlertOctagon, Info } from "lucide-react";

/**
 * A single number with its label — the right form when there is one value to
 * read and no shape to see. A chart of one number is a decorated number.
 *
 * `tone` is a state, not a palette slot: it uses the reserved status colours,
 * which never double as series colours. Status is shown with an icon as well as
 * a colour, because two of the four status hues are close enough in
 * normal-vision terms that colour alone would not distinguish them.
 */

const TONES = {
  neutral: { ring: "border-white/10", value: "text-white", icon: null },
  good: {
    ring: "border-emerald-500/25",
    value: "text-emerald-300",
    icon: CheckCircle2,
  },
  warning: {
    ring: "border-amber-500/25",
    value: "text-amber-300",
    icon: AlertTriangle,
  },
  serious: {
    ring: "border-orange-500/25",
    value: "text-orange-300",
    icon: AlertOctagon,
  },
  critical: {
    ring: "border-red-500/25",
    value: "text-red-300",
    icon: AlertOctagon,
  },
  info: { ring: "border-sky-500/25", value: "text-sky-300", icon: Info },
};

export default function StatTile({
  label,
  value,
  sub,
  tone = "neutral",
  onClick,
  title,
}) {
  const t = TONES[tone] || TONES.neutral;
  const Icon = t.icon;
  const Tag = onClick ? "button" : "div";

  return (
    <Tag
      onClick={onClick}
      title={title}
      className={`text-left w-full rounded-xl border bg-slate-900/80 p-4 transition-colors ${t.ring} ${
        onClick ? "hover:bg-slate-800/80 cursor-pointer" : ""
      }`}
    >
      <div className="flex items-center gap-1.5">
        {Icon && <Icon size={13} className={t.value} aria-hidden="true" />}
        <p className="text-xs font-medium text-slate-400">{label}</p>
      </div>
      {/* Proportional figures for a standalone number; tabular is for columns
          that must align down a table. */}
      <p className={`text-2xl font-bold mt-1.5 ${t.value}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </Tag>
  );
}
