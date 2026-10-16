import { StatusBadge } from "../ui";

/** Signal bands, matching fiber/services/analytics.py. */
export const BAND_LABEL = {
  good: "Good",
  warning: "Weak",
  critical: "Critical",
  overload: "Too strong",
  unknown: "Not reported",
};

export const CAUSE_LABEL = {
  fiber_cut: "Fiber cut",
  power_cut: "Area power cut",
  olt_down: "OLT unreachable",
  customer_power: "Customer power off",
  maintenance: "Planned maintenance",
  unknown: "Unknown",
};

export const DOWN_LABEL = { los: "Loss of signal", dying_gasp: "Power off", unknown: "Unknown" };

export const dbm = (v) => (v == null ? "—" : `${Number(v).toFixed(1)} dBm`);
export const metres = (v) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} km` : `${v} m`);
export const when = (iso) =>
  iso ? new Date(iso).toLocaleString("en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
export const minutes = (m) => (m == null ? "—" : m >= 120 ? `${Math.floor(m / 60)} h ${m % 60} m` : `${m} min`);

export function Band({ band }) {
  return <StatusBadge status={band} label={BAND_LABEL[band] || band} />;
}

export function OnuStatus({ status }) {
  return <StatusBadge status={status} />;
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1 border-b border-white/10" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            value === t.id
              ? "border-blue-400 text-white"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          {t.label}
          {t.count ? (
            <span className="ml-1.5 rounded-full bg-red-500/20 px-1.5 text-[11px] text-red-300">{t.count}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

export const inputCls =
  "w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500";
export const buttonCls =
  "inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50";
export const ghostCls =
  "inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-white/5 disabled:opacity-50";
