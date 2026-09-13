import { useQuery } from "@tanstack/react-query";
import { Chart, StatTile, gb, num } from "../admin/ui";
import {
  fetchAdminUsageDaily, fetchAdminUsageTotals,
} from "../../services/dashboard";

/**
 * Network usage across every router.
 *
 * Rebuilt on the shared kit. This was the last chart in the operator console
 * outside the design system — its own useEffect-and-useState fetch rather than
 * react-query, its own colours, its own container and its own tooltip — which
 * made the one chart an operator sees every day the one that matched nothing
 * around it.
 *
 * Gigabytes throughout. The previous version multiplied into megabytes for the
 * plot while labelling the tiles beside it in gigabytes, so the axis and the
 * figures under it disagreed by a factor of a thousand.
 *
 * On the `jade` surface, which carries its own grid, ink and marks — see
 * SURFACES in the ui tokens for why none of those could be inherited from the
 * default slate card, and what each one was measured at.
 */

const SURFACE = "jade";

const DAYS = 7;

const BYTES_IN_GB = 1024 ** 3;

// Shortest first, so the eye reads them as nested windows rather than four
// unrelated figures.
const WINDOWS = [
  { key: "today", label: "Today" },
  { key: "week", label: "This week" },
  { key: "month", label: "This month" },
  { key: "year", label: "This year" },
];

export default function AdminUsageGraph() {
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["admin-usage-daily", DAYS],
    queryFn: () => fetchAdminUsageDaily(DAYS),
    staleTime: 5 * 60 * 1000,
  });

  // The calendar windows, fetched separately from the plot.
  //
  // A seven-day chart cannot answer "how much this month" -- and the totals
  // under it used to say "over 7 days", which is the plot's window and nobody
  // else's. These come from the rollup and the raw deltas stitched together;
  // see estate_usage_totals for why neither alone is right.
  const { data: totals } = useQuery({
    queryKey: ["admin-usage-totals"],
    queryFn: () => fetchAdminUsageTotals(),
    staleTime: 5 * 60 * 1000,
  });

  if (isError) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        Couldn't load usage. Try refreshing.
      </p>
    );
  }

  if (isLoading) {
    return <div className="h-[280px] rounded-lg bg-white/5 animate-pulse" />;
  }

  const series = (data || []).map((d) => ({
    day: d.day,
    download: Number(d.download_gb || 0),
    upload: Number(d.upload_gb || 0),
  }));

  const totalDown = series.reduce((s, d) => s + d.download, 0);
  const totalUp = series.reduce((s, d) => s + d.upload, 0);

  const shortDay = (iso) =>
    new Date(iso).toLocaleDateString("en-KE", { month: "short", day: "numeric" });

  return (
    <div className="space-y-4">
      <Chart
        kind="area"
        surface={SURFACE}
        data={series}
        xKey="day"
        series={[
          { key: "download", label: "Download" },
          { key: "upload", label: "Upload" },
        ]}
        xTickFormatter={shortDay}
        yTickFormatter={(v) => `${v} GB`}
        valueFormatter={(v) => gb(v)}
        labelFormatter={shortDay}
        empty="No usage recorded in this period"
      />

      {/* What the network has carried, in the windows an operator thinks in.
          Calendar, not rolling: "this month" means since the 1st, the way a
          bill means it. Each carries its own split, because a total that does
          not say how much of it was upload hides the thing worth noticing. */}
      {totals?.periods && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {WINDOWS.map(({ key, label }) => {
            const p = totals.periods[key];
            if (!p) return null;
            return (
              <StatTile
                key={key}
                label={label}
                value={gb(p.total / BYTES_IN_GB)}
                sub={`${gb(p.download / BYTES_IN_GB)} down · ${gb(
                  p.upload / BYTES_IN_GB
                )} up`}
                surface={SURFACE}
              />
            );
          })}
        </div>
      )}

      {/* The plot's own totals, kept because they are what the chart above
          shows and the windows above them are not. */}
      {series.length > 0 && (
        <p className="text-xs text-slate-400">
          The chart covers the last {num(series.length)} days:{" "}
          {gb(totalDown)} down, {gb(totalUp)} up.
        </p>
      )}
    </div>
  );
}
