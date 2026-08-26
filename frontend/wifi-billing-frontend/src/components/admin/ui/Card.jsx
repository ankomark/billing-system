import { AlertTriangle, CheckCircle2, Info, AlertOctagon } from "lucide-react";
import { CARD_SHEEN, CHIP, SURFACES, statusStyle } from "./tokens";

/**
 * Surfaces for the operator console.
 *
 * Before this, every page invented its own: three card styles, four table
 * headers, two heading sizes, and status pills coloured wherever they happened
 * to be rendered.
 *
 * Dark, like the platform kit. The two consoles are told apart by accent now —
 * blue here, teal there — plus the impersonation banner, rather than by one
 * being light.
 */

export function Card({ children, className = "", padded = true, sheen, surface }) {
  const lit = CARD_SHEEN[sheen];
  const theme = SURFACES[surface] || null;

  if (theme) {
    const g = theme.glass;
    return (
      <div
        className={`relative overflow-hidden rounded-xl border shadow-lg shadow-black/20 ${
          padded ? "p-5" : ""
        } ${className}`}
        style={{ backgroundColor: theme.surface, borderColor: g?.ring }}
      >
        {/* The light the glass refracts, built the way StatTile builds it: a
            real blur on a real source, because a flat page gives
            `backdrop-filter` nothing behind it to work on.

            Cornered rather than centred, and deliberately so — the plot fills
            this card, and the orb's centre is the one place where a mark comes
            closest to its own background. Off in the corner it lifts the header
            and the card's edge, which is where a sheen belongs, and leaves the
            middle of the plot on the flat ground the marks were measured
            against. Its alpha is bounded by the marks regardless — see the
            note on SURFACES.jade.glass. */}
        {g && (
          <span
            className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full blur-3xl"
            style={{ backgroundColor: g.tint, opacity: g.alpha }}
            aria-hidden="true"
          />
        )}
        <div className="relative">{children}</div>
      </div>
    );
  }

  return (
    <div
      className={`rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 ${
        padded ? "p-5" : ""
      } ${className}`}
      // A background-image over the existing tint, so the card keeps its
      // surface and only gains the light. See CARD_SHEEN for the ceilings.
      style={lit ? { backgroundImage: lit } : undefined}
    >
      {children}
    </div>
  );
}

/**
 * `chip` + `icon` mark which panel this is, the same way StatTile's chip marks
 * which number. Optional throughout — a header without one is unchanged, so
 * the pages that do not pass them look exactly as they did.
 */
export function CardHeader({ title, subtitle, action, chip, icon: Icon, surface }) {
  const chipColor = CHIP[chip] || null;
  const onSurface = SURFACES[surface] || null;

  return (
    <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
      <div className="flex items-start gap-3 min-w-0">
        {Icon && chipColor && (
          <span
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
            style={{ backgroundColor: chipColor }}
            aria-hidden="true"
          >
            <Icon size={17} className="text-white" />
          </span>
        )}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          {/* Raised on a named surface: slate-400 measures 4.48:1 on jade,
              which is under 4.5 before anything is laid over it. */}
          {subtitle && (
            <p className="text-xs text-slate-400 mt-0.5" style={onSurface ? { color: onSurface.ink } : undefined}>
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {action}
    </div>
  );
}

export function Section({ title, action, children }) {
  return (
    <section>
      <div className="flex items-center justify-between gap-3 mb-3">
        <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">
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
        <h1 className="text-2xl font-bold text-white tracking-tight">{title}</h1>
        {subtitle && <p className="text-slate-400 text-sm mt-1">{subtitle}</p>}
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
  info: { cls: "bg-blue-500/10 border-blue-500/30 text-blue-200", icon: Info },
  good: { cls: "bg-emerald-500/10 border-emerald-500/30 text-emerald-200", icon: CheckCircle2 },
  warning: { cls: "bg-amber-500/10 border-amber-500/30 text-amber-200", icon: AlertTriangle },
  critical: { cls: "bg-red-500/10 border-red-500/30 text-red-200", icon: AlertOctagon },
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
