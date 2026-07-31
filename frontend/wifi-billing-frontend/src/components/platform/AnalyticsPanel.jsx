import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader, Chart, compactKES, KES, num } from "./ui";
import { fetchPlatformAnalytics } from "../../services/platform";

/**
 * The platform's trends.
 *
 * Three charts rather than one with three lines, because they measure different
 * things at different magnitudes. Putting money and headcount on one plot would
 * need a second y-axis, and a dual axis lets whoever picks the scales imply any
 * correlation they like — so it is never used here.
 */

const RANGES = [
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
  { days: 365, label: "1y" },
];

const shortDate = (iso) =>
  new Date(iso).toLocaleDateString("en-KE", { month: "short", day: "numeric" });

const fullDate = (iso) =>
  new Date(iso).toLocaleDateString("en-KE", {
    weekday: "short", year: "numeric", month: "short", day: "numeric",
  });

export default function AnalyticsPanel({ tenant, title = "Trends" }) {
  const [days, setDays] = useState(30);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["platform-analytics", days, tenant ?? "all"],
    queryFn: () => fetchPlatformAnalytics({ days, tenant }),
    staleTime: 5 * 60 * 1000,
  });

  if (isError) {
    return (
      <Card>
        <p className="text-sm text-slate-400">Couldn't load trends.</p>
      </Card>
    );
  }

  const series = data?.series ?? [];
  // Long ranges have more points than there is room for labels.
  const tickEvery = days > 90 ? 30 : days > 30 ? 7 : days > 14 ? 3 : 1;
  const xTick = (v, i) => (i % tickEvery === 0 ? shortDate(v) : "");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">
          {title}
        </h2>
        {/* Filters in one row above the charts, not per-chart. */}
        <div
          className="inline-flex rounded-lg border border-white/10 p-0.5"
          role="group"
          aria-label="Time range"
        >
          {RANGES.map((r) => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              aria-pressed={days === r.days}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
                days === r.days
                  ? "bg-white/10 text-white"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-[300px] rounded-xl border border-white/10 bg-slate-900/60 animate-pulse"
            />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Platform revenue"
              subtitle={`${KES(data.totals.platform_revenue)} collected from operators`}
            />
            <Chart
              kind="area"
              data={series}
              xKey="day"
              series={[{ key: "platform_revenue", label: "Collected" }]}
              xTickFormatter={xTick}
              yTickFormatter={compactKES}
              valueFormatter={(v) => KES(v)}
              labelFormatter={fullDate}
              empty="No payments in this period"
            />
          </Card>

          <Card>
            <CardHeader
              title="Subscriber revenue"
              subtitle={`${KES(data.totals.subscriber_revenue)} through operators' tills`}
            />
            <Chart
              kind="area"
              data={series}
              xKey="day"
              series={[{ key: "subscriber_revenue", label: "Taken" }]}
              xTickFormatter={xTick}
              yTickFormatter={compactKES}
              valueFormatter={(v) => KES(v)}
              labelFormatter={fullDate}
              empty="No subscriber payments in this period"
            />
          </Card>

          <Card>
            <CardHeader
              title="Subscribers added"
              subtitle={`${num(data.totals.subscribers_added)} in this period`}
            />
            <Chart
              kind="bar"
              data={series}
              xKey="day"
              series={[{ key: "subscribers_added", label: "New subscribers" }]}
              xTickFormatter={xTick}
              valueFormatter={(v) => num(v)}
              labelFormatter={fullDate}
              empty="Nobody signed up in this period"
            />
          </Card>

          {tenant && data.stations?.length > 0 && (
            <Card>
              <CardHeader
                title="Subscribers by station"
                subtitle="Where this operator's customers are served from"
              />
              <Chart
                kind="bar"
                data={data.stations}
                xKey="name"
                series={[{ key: "subscribers", label: "Subscribers" }]}
                valueFormatter={(v) => num(v)}
                empty="No subscribers attached to a station yet"
              />
            </Card>
          )}

          {!tenant && (
            <Card>
              <CardHeader
                title="Operators"
                subtitle="Businesses on the platform, cumulative"
              />
              <Chart
                kind="line"
                data={series}
                xKey="day"
                series={[{ key: "operators", label: "Operators" }]}
                xTickFormatter={xTick}
                valueFormatter={(v) => num(v)}
                labelFormatter={fullDate}
                empty="No operators yet"
              />
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
