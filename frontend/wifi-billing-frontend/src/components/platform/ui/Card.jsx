/**
 * Surfaces for the platform console.
 *
 * One card style, one header style. These existed as local components inside
 * PlatformOverview, so every new page reinvented them slightly differently.
 */

export function Card({ children, className = "", padded = true }) {
  return (
    <div
      className={`rounded-xl border border-white/10 bg-slate-900/80 backdrop-blur-sm shadow-lg shadow-black/20 ${
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
        <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

/** A labelled group of tiles. */
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
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

export function StatusBadge({ status, styles, label }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${
        styles[status] || styles.cancelled
      }`}
    >
      {label ?? String(status || "").replace(/_/g, " ")}
    </span>
  );
}
