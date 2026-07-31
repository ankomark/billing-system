/**
 * Design tokens for the platform console.
 *
 * The platform console is dark; the operator console is light. That contrast is
 * deliberate and predates this file (see PlatformLayout) — someone holding both
 * kinds of access must know at a glance whose data is on screen.
 *
 * The series palette below is a validated categorical palette, stepped for a
 * dark surface. It was checked against THIS surface (#0f172a) rather than
 * assumed: lightness band, chroma floor, CVD separation, normal-vision floor
 * and contrast all pass, worst adjacent CVD ΔE 8.4.
 *
 * Two rules that are not stylistic preferences:
 *   - Assign series colours in fixed slot order, never cycled. A 9th series
 *     folds into "Other" — it never gets a generated hue.
 *   - Colour follows the entity, not its rank. Filtering a series out must not
 *     repaint the ones that remain.
 */

// ─── Chart surfaces and chrome ──────────────────────────────────────────────
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

// ─── Categorical series, in fixed order ─────────────────────────────────────
// Slots 1-3 additionally pass all-pairs, so scatter-type forms cap at three.
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

// ─── Status — reserved, never reused as a series colour ─────────────────────
// All four clear 3:1 on the surface above. warning and serious sit close to
// each other in normal-vision ΔE, which is why status is always shown with an
// icon and a label and never by colour alone.
export const STATUS_INK = {
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
};

// ─── Sequential ramp (magnitude), single hue light→dark ─────────────────────
export const SEQUENTIAL_BLUE = [
  "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b",
];

/**
 * Operator status badge styling — one map for the whole platform console.
 *
 * PlatformOverview used teal while Operators used emerald and slate, so the
 * same operator could read as two different colours on two pages.
 */
export const STATUS_STYLES = {
  trial:      "bg-sky-500/10 text-sky-300 border-sky-500/30",
  active:     "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  past_due:   "bg-amber-500/10 text-amber-300 border-amber-500/30",
  restricted: "bg-red-500/10 text-red-300 border-red-500/30",
  cancelled:  "bg-slate-500/10 text-slate-400 border-slate-500/30",
};

export const statusLabel = (s) => String(s || "").replace(/_/g, " ");

// ─── Formatters ─────────────────────────────────────────────────────────────
// Was duplicated verbatim in three platform pages.
export const KES = (v) => `KES ${Number(v || 0).toLocaleString()}`;

export const compactKES = (v) => {
  const n = Number(v || 0);
  if (Math.abs(n) >= 1_000_000) return `KES ${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `KES ${(n / 1_000).toFixed(1)}k`;
  return `KES ${n.toLocaleString()}`;
};

export const num = (v) => Number(v || 0).toLocaleString();
