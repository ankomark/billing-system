/**
 * Design tokens for the operator console.
 *
 * Light, deliberately. The platform console is dark and this one is not, and
 * that difference is the whole point — someone holding both kinds of access
 * must know at a glance whose data is on screen. So this is the platform kit's
 * rigour, not its palette.
 *
 * The series palette was validated against THIS surface (#ffffff cards), not
 * inherited from the dark console: lightness band, chroma floor, CVD separation
 * and normal-vision floor all pass, worst adjacent CVD ΔE 9.1.
 *
 * One caveat that is not dismissable. Three slots — aqua, yellow and magenta —
 * measure below 3:1 against white (2.82, 2.17, 2.69). The rule for that is
 * relief: any chart using them must ship visible numbers, either as direct
 * labels or as a table beside the plot. The usage graph does this with its KPI
 * tiles, and any new chart must do the same or stay within the first two slots,
 * which both clear 3:1.
 */

export const CHROME = {
  plane: "#f8fafc",      // page background behind the cards
  surface: "#ffffff",    // card / chart surface — what the palette was validated against
  border: "#e2e8f0",
  inkPrimary: "#1e293b",
  inkSecondary: "#475569",
  inkMuted: "#94a3b8",   // axis ticks, labels
  grid: "#f1f5f9",       // hairline gridlines
  baseline: "#cbd5e1",
};

// Categorical series, fixed order, never cycled. Slots 1-3 also pass all-pairs,
// which is the cap for scatter-type forms.
export const SERIES = [
  "#2a78d6", // 1 blue
  "#eb6834", // 2 orange
  "#1baf7a", // 3 aqua      — below 3:1, needs relief
  "#eda100", // 4 yellow    — below 3:1, needs relief
  "#e87ba4", // 5 magenta   — below 3:1, needs relief
  "#008300", // 6 green
  "#4a3aa7", // 7 violet
  "#e34948", // 8 red
];

export const seriesColor = (i) => SERIES[i % SERIES.length];

/** Slots that need visible numbers beside the mark on this surface. */
export const NEEDS_RELIEF = new Set([2, 3, 4]);

// Status — reserved, never reused as a series colour, always paired with an
// icon and a label because two of these are close in normal-vision terms.
export const STATUS_INK = {
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
};

/**
 * One status vocabulary for the whole operator console.
 *
 * Customer status, subscription status, invoice status and router status were
 * each styled where they happened to be rendered, so "active" was emerald on
 * one page and green on another.
 */
export const STATUS_STYLES = {
  // customers and subscriptions
  active: "bg-emerald-50 text-emerald-700 border-emerald-200",
  expired: "bg-slate-100 text-slate-600 border-slate-200",
  suspended: "bg-amber-50 text-amber-700 border-amber-200",
  // invoices
  paid: "bg-emerald-50 text-emerald-700 border-emerald-200",
  unpaid: "bg-red-50 text-red-700 border-red-200",
  pending: "bg-amber-50 text-amber-700 border-amber-200",
  // routers and everything else
  online: "bg-emerald-50 text-emerald-700 border-emerald-200",
  offline: "bg-red-50 text-red-700 border-red-200",
  inactive: "bg-slate-100 text-slate-600 border-slate-200",
  failed: "bg-red-50 text-red-700 border-red-200",
  success: "bg-emerald-50 text-emerald-700 border-emerald-200",
};

export const statusStyle = (s) =>
  STATUS_STYLES[String(s || "").toLowerCase()] || STATUS_STYLES.inactive;

// ─── Formatters ─────────────────────────────────────────────────────────────
export const KES = (v) => `KES ${Number(v || 0).toLocaleString()}`;

export const compactKES = (v) => {
  const n = Number(v || 0);
  if (Math.abs(n) >= 1_000_000) return `KES ${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `KES ${(n / 1_000).toFixed(1)}k`;
  return `KES ${n.toLocaleString()}`;
};

export const num = (v) => Number(v || 0).toLocaleString();

export const gb = (v) => `${Number(v || 0).toFixed(1)} GB`;

export const shortDate = (v) =>
  v ? new Date(v).toLocaleDateString("en-KE", { month: "short", day: "numeric" }) : "—";

export const dateTime = (v) =>
  v ? new Date(v).toLocaleString("en-KE", {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : "—";
