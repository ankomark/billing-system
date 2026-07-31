import { AlertTriangle, CheckCircle2, Info, AlertOctagon } from "lucide-react";
import { statusStyle } from "./tokens";

/**
 * Surfaces for the operator console.
 *
 * Before this, every page invented its own: three card styles, four table
 * headers, two heading sizes, and status pills coloured wherever they happened
 * to be rendered. Same rigour as the platform kit; different palette, because
 * the two consoles must not look alike.
 */

export function Card({ children, className = "", padded = true }) {
  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white shadow-sm ${
        padded ? "p-5" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, action }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Section({ title, action, children }) {
  return (
    <section>
      <div className="flex items-center justify-between gap-3 mb-3">
        <h2 className="text-[11px] font-semibold text-slate-500 uppercase tracking-[0.14em]">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold text-slate-900 tracking-tight">{title}</h1>
        {subtitle && <p className="text-slate-500 text-sm mt-1">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

/** One status vocabulary. See STATUS_STYLES for why. */
export function StatusBadge({ status, label }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${statusStyle(status)}`}
    >
      {label ?? String(status || "").replace(/_/g, " ")}
    </span>
  );
}

const NOTE_TONES = {
  info: { cls: "bg-blue-50 border-blue-200 text-blue-900", icon: Info },
  good: { cls: "bg-emerald-50 border-emerald-200 text-emerald-900", icon: CheckCircle2 },
  warning: { cls: "bg-amber-50 border-amber-200 text-amber-900", icon: AlertTriangle },
  critical: { cls: "bg-red-50 border-red-200 text-red-900", icon: AlertOctagon },
};

/**
 * An inline explanation or warning. Always carries an icon as well as a colour,
 * because two of the status hues are close enough that colour alone would not
 * separate them.
 */
export function Note({ tone = "info", title, children }) {
  const t = NOTE_TONES[tone] || NOTE_TONES.info;
  const Icon = t.icon;
  return (
    <div className={`rounded-xl border px-5 py-4 flex gap-3 ${t.cls}`}>
      <Icon size={18} className="flex-shrink-0 mt-0.5" aria-hidden="true" />
      <div className="text-sm min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={title ? "mt-1" : ""}>{children}</div>}
      </div>
    </div>
  );
}
