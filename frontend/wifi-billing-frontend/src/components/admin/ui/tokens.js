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

/**
 * Solid icon chips, for telling one metric from another at a glance.
 *
 * The same validated hues as SERIES, named so a caller asks for a colour by
 * identity rather than by position in an array — a tile keeps its hue when the
 * one above it is removed.
 *
 * These carry a WHITE icon. Measured against each fill, not assumed: blue 3.64,
 * orange 3.88, aqua 3.41, magenta 3.94, green 4.95, red 3.23, violet 3.13.
 * All clear the 3:1 that non-text graphics need. Yellow (#c98500) is left out
 * at 3.07 — passing, but with nothing left over for a lighter screen.
 *
 * A chip is decoration with a job: it marks WHICH number this is, never how the
 * number is doing. State stays with `tone` on the tile, which is why a tile
 * that has a tone takes its chip from the status palette instead — one signal,
 * not two competing ones.
 */
/**
 * A lit edge across a card, for the few cards that are actions rather than
 * readouts. Sparingly: if every card has one, none of them stands out.
 *
 * Ceilings measured against the slate-400 subtitle on bg-slate-900/80 over the
 * page — azure 0.33, white 0.15 — and each peak sits under its own. Azure
 * arrives from the right because that is where the card's own action sits;
 * pearl crosses the corner, which is what a sheen does on something polished.
 */
export const CARD_SHEEN = {
  azure:
    "linear-gradient(90deg, rgba(0,128,255,0) 30%, rgba(0,128,255,0.12) 62%, rgba(0,128,255,0.26) 100%)",
  pearl:
    "linear-gradient(115deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0.03) 38%, rgba(255,255,255,0) 62%)",
};

/**
 * Tinted glass for a card surface.
 *
 * A flat dark page gives `backdrop-filter` nothing to refract, so glass here is
 * built the way it actually reads: a soft blurred orb of colour sitting inside
 * a translucent card, with a hue-matched hairline. The blur is a real blur on a
 * real light source, not a backdrop filter with nothing behind it.
 *
 * `alpha` is the orb at its centre — the card's lightest point, and therefore
 * the only place worth measuring. Each was solved for rather than picked: the
 * ceiling is the highest alpha at which BOTH text steps still clear 4.5:1 on
 * bg-slate-900/70 over the slate-950 page, and each value sits a little under
 * its own ceiling so rounding and antialiasing cannot tip it.
 *
 *   pearl  ceiling 0.16   using 0.14
 *   azure  ceiling 0.34   using 0.30
 *   ruby   ceiling 0.51   using 0.44
 *
 * They differ by that much because white lightens a dark card fastest and a
 * deep red barely lightens it at all — the same alpha is a different amount of
 * damage per hue, which is exactly why one global opacity would have been
 * wrong. Pearl is the quiet one by nature, not by preference.
 *
 * The binding constraint is the small `sub` line, not the value or the label.
 * Raise the sub ink and every ceiling here moves with it.
 *
 * These are decoration, deliberately NOT drawn from SERIES. A card's background
 * is not a data mark, and taking a categorical slot for it would mean a chart
 * and a card could claim the same hue for two unrelated reasons.
 */
export const GLASS = {
  pearl: { tint: "#ffffff", alpha: 0.14, ring: "rgba(255,255,255,0.22)" },
  azure: { tint: "#0080ff", alpha: 0.30, ring: "rgba(56,150,255,0.40)" },
  ruby: { tint: "#c81e3a", alpha: 0.44, ring: "rgba(220,60,90,0.42)" },
};

/**
 * Named chart surfaces.
 *
 * A card that is not the default #0f172a cannot borrow the default chrome: the
 * grid, the tick ink and the series were each measured against that colour, and
 * every one of them moves when the surface does. This keeps such a surface
 * together as one object — its own grid, its own ink, its own marks — so a card
 * either takes all of it or none of it, and the rest of the console is not
 * touched by a change made for one panel.
 *
 * ── jade ────────────────────────────────────────────────────────────────────
 * #0C4137 is a far lighter ground than the default: relative luminance 0.0413
 * against 0.0090, four and a half times as bright. Three things follow, none of
 * them optional, all of them measured rather than judged:
 *
 * 1. THE MARKS. A mark needs 3:1 against its surface. On this ground a literal
 *    dark green does not get there — the palette's own #008300 lands at 2.32.
 *    Nor can two greens carry two series: every pair inside the L 0.48–0.67
 *    band tops out around ΔE 14.9 in normal vision, under the 15 floor, and
 *    that floor is the one secondary encoding does not excuse. So download is
 *    green and upload is the palette's existing gold. Green and gold on deep
 *    forest is also the register this card was asked for.
 *        #00a578 3.64:1 · #c98500 3.74:1 · pair ΔE 20.1 normal, 9.5 worst CVD.
 *
 * 2. THE GRID. #1e293b is LIGHTER than the default surface and DARKER than this
 *    one, so carrying it over would flip a faint hairline into a dark scratch
 *    across the plot. The jade grid is a step up from its ground, as a recessive
 *    gridline is everywhere else.
 *
 * 3. THE INK. Axis ticks at #94a3b8 measure 4.48:1 here — under 4.5 before a
 *    single pixel of glass is added. Both text steps move up one, exactly as
 *    the note on GLASS predicts: "raise the sub ink and every ceiling moves
 *    with it." That is what buys the glass any room at all.
 */
export const SURFACES = {
  jade: {
    surface: "#0C4137",
    // A step up from the ground, not the slate grid, which would be darker
    // than it. 1.35:1 — present when looked for, gone when not.
    grid: "#17564a",
    baseline: "#20705f",
    // Raised one step. #94a3b8 is 4.48:1 here; this is 7.74:1.
    ink: "#cbd5e1",
    inkStrong: "#e2e8f0",
    // Fixed order, as everywhere: slot 0 download, slot 1 upload.
    series: ["#00a578", "#c98500"],
    /**
     * The orb, and the one number here that is NOT bounded by text.
     *
     * On a stat tile the binding constraint is the small `sub` line, because
     * text is most of what a tile contains. On a chart card the plot covers the
     * surface, so the binding constraint is the MARKS — and they bind much
     * harder. With a mint tint the text ceiling is 0.220 and the mark ceiling
     * is 0.080, so the marks decide it and the usual reasoning about ink would
     * have picked a value nearly three times too strong.
     *
     * At 0.07, a little under that ceiling: the marks hold 3.08:1 and 3.17:1
     * at the orb's brightest point, and the raised sub ink sits at 6.55:1.
     * Above roughly 0.08 the green line starts to dissolve into its own
     * background exactly where the glass is prettiest.
     */
    glass: { tint: "#5eead4", alpha: 0.07, ring: "rgba(94,234,212,0.34)" },
    // Tiles sitting ON this card. A white lift rather than a slate fill, which
    // would go muddy over green.
    raise: "rgba(255,255,255,0.06)",
  },
};

export const CHIP = {
  blue: "#3987e5",
  orange: "#d95926",
  aqua: "#199e70",
  magenta: "#d55181",
  green: "#008300",
  violet: "#9085e9",
  red: "#e66767",
};

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
