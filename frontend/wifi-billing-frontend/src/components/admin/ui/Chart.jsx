import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { CHROME, seriesColor } from "./tokens";

/**
 * One chart primitive for the operator console.
 *
 * The same rules as the platform one, restated for a light surface rather than
 * duplicated by accident — before either existed there were three chart
 * implementations across this app with two container styles, three tooltip
 * approaches, and two different colour pairs meaning the same download/upload
 * distinction.
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
 * On this surface three palette slots fall below 3:1, so `relief` is required
 * whenever more than two series are shown: pass the visible numbers that sit
 * beside the plot (KPI tiles, a table) and the component will not silently
 * render an unreadable chart without them.
 */

function TooltipCard({ active, payload, label, formatter, labelFormatter }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-lg">
      <p className="text-xs font-medium text-slate-600 mb-1.5">
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
            <span className="text-slate-500">{p.name}</span>
            <span className="ml-auto font-semibold text-slate-800 tabular-nums">
              {formatter ? formatter(p.value, p.dataKey) : p.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const axisProps = {
  stroke: CHROME.inkMuted,
  tickLine: false,
  axisLine: false,
  tickMargin: 10,
  tick: { fontSize: 11, fill: CHROME.inkMuted },
};

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
  relief = false,
}) {
  if (!data.length) {
    return (
      <div style={{ height }} className="flex items-center justify-center text-sm text-slate-400">
        {empty}
      </div>
    );
  }

  if (series.length > 2 && !relief) {
    // Deliberately loud rather than quietly unreadable. Slots 3-5 sit below
    // 3:1 on white, and the rule for that is visible numbers beside the plot.
    // eslint-disable-next-line no-console
    console.warn(
      "[Chart] More than two series on a light surface needs relief " +
        "(visible numbers beside the plot). Pass relief when they are present."
    );
  }

  const showLegend = series.length >= 2;
  const ChartTag = kind === "bar" ? BarChart : ComposedChart;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ChartTag data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          {series.map((s, i) => (
            <linearGradient key={s.key} id={`adm-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={seriesColor(i)} stopOpacity={0.22} />
              <stop offset="100%" stopColor={seriesColor(i)} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>

        <CartesianGrid strokeDasharray="3 3" stroke={CHROME.grid} vertical={false} />
        <XAxis dataKey={xKey} {...axisProps} tickFormatter={xTickFormatter} />
        <YAxis {...axisProps} tickFormatter={yTickFormatter} width={56} />
        <Tooltip
          cursor={{ stroke: CHROME.baseline, strokeWidth: 1 }}
          content={<TooltipCard formatter={valueFormatter} labelFormatter={labelFormatter} />}
        />
        {showLegend && (
          <Legend
            iconType="square"
            iconSize={9}
            wrapperStyle={{ fontSize: 11, color: CHROME.inkSecondary, paddingTop: 8 }}
          />
        )}

        {series.map((s, i) => {
          const color = seriesColor(i);
          if (kind === "bar") {
            return (
              <Bar
                key={s.key} dataKey={s.key} name={s.label} fill={color}
                stackId={stacked ? "a" : undefined}
                radius={[4, 4, 0, 0]}
                // A 2px gap of surface between adjacent fills keeps touching
                // marks countable.
                stroke={CHROME.surface} strokeWidth={2}
              />
            );
          }
          if (kind === "line") {
            return (
              <Line
                key={s.key} type="monotone" dataKey={s.key} name={s.label}
                stroke={color} strokeWidth={2} dot={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: CHROME.surface }}
              />
            );
          }
          return (
            <Area
              key={s.key} type="monotone" dataKey={s.key} name={s.label}
              stroke={color} strokeWidth={2} fill={`url(#adm-${s.key})`}
              stackId={stacked ? "a" : undefined} dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: CHROME.surface }}
            />
          );
        })}
      </ChartTag>
    </ResponsiveContainer>
  );
}
