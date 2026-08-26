import { useQuery } from "@tanstack/react-query";
import { Chart, StatTile, gb, num } from "../admin/ui";
import { fetchAdminUsageDaily } from "../../services/dashboard";

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

export default function AdminUsageGraph() {
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["admin-usage-daily", DAYS],
    queryFn: () => fetchAdminUsageDaily(DAYS),
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

      {/* The totals beside the plot. Also what makes a two-series chart
          readable without leaning on colour alone. */}
      {series.length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          <StatTile label="Downloaded" value={gb(totalDown)} surface={SURFACE} />
          <StatTile label="Uploaded" value={gb(totalUp)} surface={SURFACE} />
          <StatTile
            label="Total"
            value={gb(totalDown + totalUp)}
            sub={`over ${num(series.length)} days`}
            surface={SURFACE}
          />
        </div>
      )}
    </div>
  );
}
