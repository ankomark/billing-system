import { Activity } from "lucide-react";

/**
 * The headline figures, given the weight they earn.
 *
 * Three equal tiles said "today", "this month" and "this year" in the same
 * voice as the four counts below them, so the first thing an operator wants to
 * know had no more presence than the number of pending invoices. This is the
 * hero-number form: one band, the largest type on the page, nothing competing.
 *
 * Royal green, and green is the one decorative hue here that is not arbitrary:
 * this band is money, and money is green nearly everywhere someone might have
 * seen a number before. The console ACCENT is still blue — sidebar, buttons,
 * chips, charts — so the thing that separates this console from the teal
 * platform one is untouched. Deliberately deep rather than bright for the same
 * reason: teal-600 and a light emerald are close enough to confuse at a glance,
 * and emerald-800 through emerald-900 is nobody's teal.
 *
 * The gradient is chrome, not data. It encodes nothing, which is exactly why
 * it is allowed to be decorative: no reader has to decode it.
 *
 * The stops are picked by measurement, not by eye. A gradient is only as
 * readable as its LIGHTEST point, which is where white gets into trouble:
 * emerald-600 gives white 3.77:1 and the labels 3.32:1, both under the 4.5:1
 * this size of text needs. emerald-700 is the first stop that carries
 * everything — white 5.48, emerald-100 4.84 — so the sweep starts there and
 * only gets darker. Labels ride at emerald-100 rather than a dimmed white so
 * they hold up at that lighter end.
 *
 * Deliberately no "+9.9% vs yesterday" deltas, though the design that inspired
 * this has them: revenue_summary() returns three totals and no comparison, and
 * a percentage with nothing behind it is worse than no percentage.
 */
export default function PulseBanner({ title = "Money in", items = [], loading }) {
  if (loading) {
    return <div className="h-[132px] rounded-2xl bg-slate-900/80 animate-pulse" />;
  }

  return (
    <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-emerald-700 via-emerald-800 to-emerald-900 shadow-lg shadow-emerald-950/40">
      {/* A soft highlight so the band reads as a surface rather than a flat
          fill. Pure decoration, and behind the text at low opacity so it
          cannot eat into the contrast measured above. */}
      <div
        className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-white/10 blur-2xl"
        aria-hidden="true"
      />

      <div className="relative px-5 py-4 sm:px-6 sm:py-5">
        <div className="flex items-center gap-2 mb-4">
          <Activity size={14} className="text-emerald-100" aria-hidden="true" />
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-emerald-100">
            {title}
          </h2>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 sm:gap-0">
          {items.map((item, i) => (
            <div
              key={item.label}
              className={
                // Hairline separators between columns, not around them, so the
                // three read as one band rather than three cards again.
                i > 0 ? "sm:border-l sm:border-white/20 sm:pl-6" : "sm:pr-6"
              }
            >
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-emerald-100">
                {item.label}
              </p>
              <p className="mt-1 text-2xl sm:text-3xl font-bold text-white tracking-tight">
                {item.value}
              </p>
              {item.sub && (
                <p className="mt-1 text-xs text-emerald-100">{item.sub}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
