/**
 * Design tokens for the operator console.
 *
 * Dark, matching the platform console. It was light until now, on the argument
 * that the two consoles must look different so someone holding both kinds of
 * access knows whose data is on screen. That job has not gone away — it is
 * carried by the accent instead: this console is BLUE throughout, the platform
 * one is TEAL, and the impersonation banner stays loud above everything.
 *
 * The palette was re-validated against this surface rather than assumed to
 * invert. All eight slots pass, worst adjacent CVD ΔE 8.4 — and every slot now
 * clears 3:1, so the relief rule that constrained three of them on white no
 * longer applies here.
 */

export const CHROME = {
  plane: "#020617",      // page background behind the cards
  surface: "#0f172a",    // card / chart surface — what the palette was validated against
  border: "rgba(255,255,255,0.08)",
  inkPrimary: "#f8fafc",
  inkSecondary: "#cbd5e1",
  inkMuted: "#94a3b8",   // axis ticks, labels
  grid: "#1e293b",       // hairline gridlines
  baseline: "#334155",
};

/** This console's accent. Teal belongs to the platform; blue belongs here. */
export const ACCENT = "#3b82f6";

// Categorical series, fixed order, never cycled. Slots 1-3 also pass all-pairs,
// which is the cap for scatter-type forms.
export const SERIES = [
  "#3987e5", // 1 blue
  "#d95926", // 2 orange
  "#199e70", // 3 aqua
  "#c98500", // 4 yellow
  "#d55181", // 5 magenta
  "#008300", // 6 green
  "#9085e9", // 7 violet
  "#e66767", // 8 red
];

export const seriesColor = (i) => SERIES[i % SERIES.length];

/** Empty on this surface — every slot clears 3:1 against #0f172a. */
export const NEEDS_RELIEF = new Set();

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
  active: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  expired: "bg-slate-500/10 text-slate-400 border-slate-500/30",
  suspended: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  // invoices
  paid: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  unpaid: "bg-red-500/10 text-red-300 border-red-500/30",
  pending: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  // routers and everything else
  online: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  offline: "bg-red-500/10 text-red-300 border-red-500/30",
  inactive: "bg-slate-500/10 text-slate-400 border-slate-500/30",
  failed: "bg-red-500/10 text-red-300 border-red-500/30",
  success: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
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
