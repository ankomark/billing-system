import { AlertTriangle, CheckCircle2, AlertOctagon, Info } from "lucide-react";
import { CHIP } from "./tokens";

/**
 * One number and its label.
 *
 * The right form when there is a single value to read and no shape to see — a
 * chart of one number is a decorated number.
 *
 * `tone` is a state, not a palette slot: it uses the reserved status colours,
 * which never double as series colours, and always pairs one with an icon.
 *
 * `chip` is the other thing colour can do here — say WHICH number this is. It
 * paints the icon square only, never the value, so the old fault does not come
 * back: stat cards were once blue, emerald, violet, amber and red by position,
 * which made "This year" violet and said nothing. A hue tied to a metric
 * survives its neighbours being removed; a hue tied to position does not.
 *
 * The two never compete. A tile with a tone takes its chip from the status
 * colour, so a tile is either saying "this is revenue" or "this needs doing",
 * and never both in two different colours at once.
 */

const TONES = {
  neutral: { ring: "border-white/10", value: "text-white", icon: null, chip: null, wash: "" },
  good: {
    ring: "border-emerald-500/30", value: "text-emerald-300",
    icon: CheckCircle2, chip: "#0ca30c", wash: "bg-emerald-500/[0.07]",
  },
  warning: {
    ring: "border-amber-500/30", value: "text-amber-300",
    icon: AlertTriangle, chip: "#fab219", wash: "bg-amber-500/[0.07]",
  },
  serious: {
    ring: "border-orange-500/30", value: "text-orange-300",
    icon: AlertOctagon, chip: "#ec835a", wash: "bg-orange-500/[0.07]",
  },
  critical: {
    ring: "border-red-500/30", value: "text-red-300",
    icon: AlertOctagon, chip: "#d03b3b", wash: "bg-red-500/[0.07]",
  },
  info: {
    ring: "border-blue-500/30", value: "text-blue-300",
    icon: Info, chip: "#3987e5", wash: "bg-blue-500/[0.07]",
  },
};

export default function StatTile({
  label, value, sub, tone = "neutral", chip, icon: ChipIcon, onClick, title,
}) {
  const t = TONES[tone] || TONES.neutral;
  const ToneIcon = t.icon;
  const Tag = onClick ? "button" : "div";

  // State wins the hue when there is one. See the note above.
  const chipColor = t.chip || CHIP[chip] || null;
  const showChip = ChipIcon && chipColor;

  return (
    <Tag
      onClick={onClick}
      title={title}
      className={`w-full text-left rounded-xl border bg-slate-900/80 p-4 shadow-sm transition-colors ${t.ring} ${t.wash} ${
        onClick ? "hover:bg-white/5 cursor-pointer" : ""
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Hidden below sm, where these tiles sit two to a row and the chip's
            36px plus its gap would take a third of the column away from
            "Unpaid invoices". The chip says which metric this is; the label
            says the same thing in words, and the words win when only one fits.
            It also means a phone renders exactly what it rendered before the
            chip existed. */}
        {showChip && (
          <span
            className="hidden sm:flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
            style={{ backgroundColor: chipColor }}
            aria-hidden="true"
          >
            {/* White, because every chip fill was measured to carry it. */}
            <ChipIcon size={17} className="text-white" />
          </span>
        )}
        {/* flex-1 as well as min-w-0: min-w-0 alone lets this column shrink
            past its content and, in a narrow grid cell, all the way to zero. */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {/* The status icon stays even when a chip is present: colour alone
                never carries state, and the chip is not state. */}
            {ToneIcon && <ToneIcon size={13} className={t.value} aria-hidden="true" />}
            <p className="text-xs font-medium text-slate-400 truncate">{label}</p>
          </div>
          {/* Proportional figures for a standalone number; tabular is for columns
              that must align down a table. */}
          <p className={`text-2xl font-bold mt-1 ${t.value}`}>{value}</p>
          {sub && <p className="text-xs text-slate-500 mt-0.5 truncate">{sub}</p>}
        </div>
      </div>
    </Tag>
  );
}
