import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { CHROME, SURFACES, seriesColor } from "./tokens";

/**
 * One chart primitive for the operator console.
 *
 * The same rules as the platform one, kept as a separate instance because the
 * two consoles carry different accents — before either existed there were three
 * chart implementations across this app with two container styles, three
 * tooltip approaches, and two different colour pairs meaning the same
 * download/upload distinction.
 *
 * Baked in rather than left to each call site:
 *   - ONE y-axis, never two. Two measures of different magnitude are two
 *     charts, or indexed to a common base — a dual axis lets the author imply
 *     any correlation they like by choosing the scales.
 *   - Recessive chrome: hairline grid, no axis lines, muted ticks.
 *   - Thin marks: 2px strokes, no dot per point, a larger dot on hover.
 *   - A legend for two or more series, so identity is never colour-alone.
 *   - A crosshair tooltip by default.
 *
 * `relief` is no longer required here. It existed because three palette slots
 * fell below 3:1 against white; on this dark surface every slot clears it. The
 * prop is kept so call sites that pass it still work, and so the rule is easy
 * to reinstate if this console ever goes light again.
 */

function TooltipCard({ active, payload, label, formatter, labelFormatter, theme }) {
  if (!active || !payload?.length) return null;
  return (
    <div
      className="rounded-lg border border-white/10 px-3 py-2 shadow-lg backdrop-blur-sm"
      // On a named surface the tooltip is that surface, a shade deeper. A slate
      // card floating over a jade one reads as a different application.
      style={theme ? { backgroundColor: theme.surface, borderColor: theme.glass?.ring } : undefined}
    >
      <p className="text-xs font-medium text-slate-300 mb-1.5" style={theme ? { color: theme.inkStrong } : undefined}>
        {labelFormatter ? labelFormatter(label) : label}
      </p>
      <div className="space-y-1">
        {payload.map((p) => (
          <div key={p.dataKey} className="flex items-center gap-2 text-xs">
            {/* The swatch carries identity; the text stays in ink tokens. */}
            <span
              className="inline-block h-2 w-2 rounded-[2px] flex-shrink-0"
              style={{ background: p.color }}
              aria-hidden="true"
            />
            <span className="text-slate-400" style={theme ? { color: theme.ink } : undefined}>{p.name}</span>
            <span className="ml-auto font-semibold text-white tabular-nums">
              {formatter ? formatter(p.value, p.dataKey) : p.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const axisFor = (ink) => ({
  stroke: ink,
  tickLine: false,
  axisLine: false,
  tickMargin: 10,
  tick: { fontSize: 11, fill: ink },
});

export default function Chart({
  kind = "area",
  data = [],
  xKey,
  series = [],
  height = 280,
  valueFormatter,
  labelFormatter,
  xTickFormatter,
  yTickFormatter,
  empty = "Nothing to show yet",
  stacked = false,
  // A named entry in SURFACES. Its grid, ink and marks were measured together
  // against its own ground, so it is taken whole or not at all — see tokens.
  surface,
  // Accepted and unused on this surface — see the note above.
  relief = false, // eslint-disable-line no-unused-vars
}) {
  if (!data.length) {
    return (
      <div style={{ height }} className="flex items-center justify-center text-sm text-slate-500">
        {empty}
      </div>
    );
  }

  const theme = SURFACES[surface] || null;
  const plane = theme?.surface ?? CHROME.surface;
  const gridInk = theme?.grid ?? CHROME.grid;
  const baseInk = theme?.baseline ?? CHROME.baseline;
  const axisProps = axisFor(theme?.ink ?? CHROME.inkMuted);
  // Fixed order within the surface's own marks, falling back to the console
  // palette. Never cycled, so a series keeps its hue if a sibling is removed.
  const markColor = (i) => theme?.series?.[i] ?? seriesColor(i);

  const showLegend = series.length >= 2;
  const ChartTag = kind === "bar" ? BarChart : ComposedChart;
  // Gradient ids are global to the document. Two charts on one page with the
  // same series keys would otherwise share a fill, and the second would silently
  // paint itself in the first one's colour.
  const gid = (key) => `adm-${surface || "base"}-${key}`;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ChartTag data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          {series.map((s, i) => (
            <linearGradient key={s.key} id={gid(s.key)} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={markColor(i)} stopOpacity={0.22} />
              <stop offset="100%" stopColor={markColor(i)} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>

        <CartesianGrid strokeDasharray="3 3" stroke={gridInk} vertical={false} />
        <XAxis dataKey={xKey} {...axisProps} tickFormatter={xTickFormatter} />
        <YAxis {...axisProps} tickFormatter={yTickFormatter} width={56} />
        <Tooltip
          cursor={{ stroke: baseInk, strokeWidth: 1 }}
          content={<TooltipCard formatter={valueFormatter} labelFormatter={labelFormatter} theme={theme} />}
        />
        {showLegend && (
          <Legend
            iconType="square"
            iconSize={9}
            wrapperStyle={{ fontSize: 11, color: theme?.ink ?? CHROME.inkSecondary, paddingTop: 8 }}
          />
        )}

        {series.map((s, i) => {
          const color = markColor(i);
          if (kind === "bar") {
            return (
              <Bar
                key={s.key} dataKey={s.key} name={s.label} fill={color}
                stackId={stacked ? "a" : undefined}
                radius={[4, 4, 0, 0]}
                // A 2px gap of surface between adjacent fills keeps touching
                // marks countable.
                stroke={plane} strokeWidth={2}
              />
            );
          }
          if (kind === "line") {
            return (
              <Line
                key={s.key} type="monotone" dataKey={s.key} name={s.label}
                stroke={color} strokeWidth={2} dot={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: plane }}
              />
            );
          }
          return (
            <Area
              key={s.key} type="monotone" dataKey={s.key} name={s.label}
              stroke={color} strokeWidth={2} fill={`url(#${gid(s.key)})`}
              stackId={stacked ? "a" : undefined} dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: plane }}
            />
          );
        })}
      </ChartTag>
    </ResponsiveContainer>
  );
}
