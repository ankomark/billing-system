import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock, TrendingDown, TrendingUp, Users } from "lucide-react";
import {
  Card, CardHeader, Chart, DataTable, StatTile,
  KES, compactKES, num, seriesColor, SURFACES,
} from "./ui";
import { fetchAnalytics } from "../../services/dashboard";
import { fetchStations } from "../../services/routers";

/**
 * The analytics panels.
 *
 * One implementation, rendered in two places: the Analytics page in full, and
 * the dashboard below the usage card. A second copy is how the two drift apart,
 * which is a mistake this codebase has already made more than once.
 *
 * `compact` drops the pulse and the totals tiles. On the dashboard those repeat
 * figures already shown above them, and the same number twice on one screen
 * makes a reader stop to check whether the two disagree.
 *
 * One request feeds every panel, so no two can be describing different moments.
 */

const RANGES = [
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
];

export default function AnalyticsPanels({ compact = false, defaultDays = 30 }) {
  const [days, setDays] = useState(defaultDays);
  const [station, setStation] = useState("");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["operator-analytics", days, station || "all"],
    queryFn: () => fetchAnalytics({ days, station: station || undefined }),
    staleTime: 2 * 60 * 1000,
  });

  const { data: stations = [] } = useQuery({
    queryKey: ["stations"],
    queryFn: fetchStations,
    staleTime: 5 * 60 * 1000,
  });

  if (isError) {
    return (
      <Card>
        <p className="text-sm text-slate-400">Couldn't load analytics.</p>
      </Card>
    );
  }

  const shortDay = (iso) =>
    new Date(iso).toLocaleDateString("en-KE", { month: "short", day: "numeric" });
  const tickEvery = days > 30 ? 7 : days > 14 ? 3 : 1;

  return (
    <div className="space-y-6">
      {/* Filters in one row above everything, rather than per panel. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">
          {compact ? "Trends" : "Performance"}
        </h2>
        <div className="flex items-center gap-2">
          {stations.length > 1 && (
            <select
              value={station}
              onChange={(e) => setStation(e.target.value)}
              aria-label="Filter by station"
              className="rounded-lg border border-white/15 bg-slate-950 px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All stations</option>
              {stations.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          )}
          <div className="inline-flex rounded-lg border border-white/10 p-0.5" role="group">
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
      </div>

      {isLoading || !data ? (
        <div className="h-40 rounded-xl border border-white/10 bg-slate-900/60 animate-pulse" />
      ) : (
        <>
          {!compact && (
            <>
              {/* Each figure carries its comparison, because "Sh 5,390 today"
                  is not information until it sits beside yesterday. */}
              <div className="rounded-xl border border-white/10 bg-gradient-to-r from-blue-900/40 to-slate-900 p-5 sm:p-6">
                <div className="grid gap-6 sm:grid-cols-3">
                  <Pulse label="Today" {...data.pulse.today} big />
                  <Pulse label="Month to date" {...data.pulse.month_to_date} big />
                  <Pulse label="Last 30 days" {...data.pulse.last_30_days} />
                </div>
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile label="Revenue in range" value={KES(data.totals.revenue)} />
                <StatTile label="Transactions" value={num(data.totals.transactions)} />
                <StatTile label="Active customers" value={num(data.totals.active_customers)} />
                <StatTile
                  label="Revenue per customer"
                  value={KES(data.totals.arpu)}
                  sub="active customers"
                />
              </div>
            </>
          )}

          {/* The two revenue panels wear the ledger surface — see SURFACES.ledger.
              Everything else on this page stays on the default ground, which is
              what makes the pair read as a set rather than as decoration.

              padded={false}: the header band is full-bleed, so the card cannot
              carry the padding — each region pads itself. */}
          <Card surface="ledger" padded={false}>
            <CardHeader
              surface="ledger"
              title="Daily revenue"
              subtitle={`${data.range.days} days · ${num(data.totals.transactions)} transactions · ${KES(data.totals.revenue)}`}
            />
            <div className="px-5 pb-5 pt-4">
              <Chart
                surface="ledger"
                kind="bar"
                data={data.series}
                xKey="day"
                series={[{ key: "revenue", label: "Revenue" }]}
                xTickFormatter={(v, i) => (i % tickEvery === 0 ? shortDay(v) : "")}
                yTickFormatter={compactKES}
                valueFormatter={(v) => KES(v)}
                labelFormatter={shortDay}
                empty="No payments in this period"
              />
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card surface="ledger" padded={false}>
              <CardHeader
                surface="ledger"
                title="What sells"
                subtitle="Packages by revenue, with the volume behind each"
              />
              <div className="px-5 pb-5 pt-4">
                <RankedBars
                  surface="ledger"
                  rows={data.by_package}
                  labelKey="name"
                  valueKey="revenue"
                  meta={(r) => `${num(r.purchases)} purchases · ${num(r.customers)} customers`}
                />
              </div>
            </Card>

            <Card>
              <CardHeader
                title="When they buy"
                subtitle="Purchases by hour — when to have credit loaded and staff on"
              />
              <Chart
                kind="bar"
                data={data.peak_hours}
                xKey="hour"
                series={[{ key: "purchases", label: "Purchases" }]}
                xTickFormatter={(h) => (h % 3 === 0 ? `${h}:00` : "")}
                valueFormatter={(v) => `${v} purchases`}
                labelFormatter={(h) => `${h}:00 – ${h}:59`}
                height={240}
                empty="No purchases in this period"
              />
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                title="About to lapse"
                subtitle="Revenue you can still act on, unlike what has already arrived"
              />
              <div className="grid grid-cols-3 gap-3">
                <AtRisk
                  label="Expiring today"
                  icon={Clock}
                  tone="warning"
                  bucket={data.expiring.today}
                />
                <AtRisk
                  label="Next 7 days"
                  icon={Clock}
                  tone="info"
                  bucket={data.expiring.next_7_days}
                />
                <AtRisk
                  label="Lapsed last 7d"
                  icon={TrendingDown}
                  tone="critical"
                  bucket={data.expiring.expired_last_7_days}
                />
              </div>
            </Card>

            <Card>
              <CardHeader title="Joined and lost" subtitle="Movement in this period" />
              <div className="grid grid-cols-2 gap-3">
                <Flow
                  label="Joined"
                  icon={Users}
                  tone="good"
                  count={data.flow.joined.count}
                  value={data.flow.joined.value}
                />
                <Flow
                  label="Lapsed"
                  icon={TrendingDown}
                  tone="critical"
                  count={data.flow.lapsed.count}
                  value={data.flow.lapsed.value}
                />
              </div>
              <p
                className={`mt-3 rounded-lg border px-4 py-2.5 text-sm text-center ${
                  data.flow.net_value >= 0
                    ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
                    : "border-red-500/25 bg-red-500/10 text-red-300"
                }`}
              >
                {data.flow.net_value >= 0 ? "Net gain " : "Net loss "}
                <strong>{KES(Math.abs(data.flow.net_value))}</strong>
              </p>
            </Card>
          </div>

          {data.by_method.length > 0 && (
            <Card>
              <CardHeader title="How they pay" subtitle="By method" />
              <RankedBars
                rows={data.by_method}
                labelKey="method"
                valueKey="revenue"
                meta={(r) => `${num(r.count)} payments`}
              />
            </Card>
          )}

          {/* Only when there is more than one site — a single row saying what
              the totals already said is noise. */}
          {data.by_station.length > 0 && (
            <Card padded={false}>
              <div className="px-5 pt-5">
                <CardHeader title="By station" subtitle="Each site in this period" />
              </div>
              <DataTable
                columns={[
                  { key: "name", label: "Station", className: "font-medium text-slate-100" },
                  {
                    key: "revenue", label: "Revenue", align: "right",
                    render: (r) => KES(r.revenue),
                  },
                  {
                    key: "customers", label: "Customers", align: "right",
                    render: (r) => num(r.customers),
                  },
                  {
                    key: "routers_offline", label: "Offline", align: "right",
                    render: (r) =>
                      r.routers_offline > 0 ? (
                        <span className="text-red-300 font-medium">{r.routers_offline}</span>
                      ) : (
                        <span className="text-slate-500">—</span>
                      ),
                  },
                ]}
                rows={data.by_station}
                empty="No stations yet"
              />
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Pulse({ label, amount, delta, against, big }) {
  const up = delta != null && delta >= 0;
  return (
    <div>
      <p className="text-[11px] font-semibold text-blue-200/60 uppercase tracking-[0.14em]">
        {label}
      </p>
      <p className={`font-bold text-white mt-1 ${big ? "text-3xl" : "text-2xl"}`}>
        {KES(amount)}
      </p>
      {/* No delta when there is nothing to compare against. Printing +100%
          because the prior period was empty would be a claim, not a fact. */}
      {delta == null ? (
        <p className="text-xs text-slate-400 mt-1">no earlier period to compare</p>
      ) : (
        <p className="text-xs mt-1 flex items-center gap-1.5">
          <span className={up ? "text-emerald-300" : "text-red-300"}>
            {up ? (
              <TrendingUp size={12} className="inline" aria-hidden="true" />
            ) : (
              <TrendingDown size={12} className="inline" aria-hidden="true" />
            )}{" "}
            {up ? "+" : ""}
            {delta}%
          </span>
          <span className="text-slate-400">{against}</span>
        </p>
      )}
    </div>
  );
}

/**
 * Horizontal bars with the number beside each.
 *
 * Chosen over a pie: comparing lengths against a shared baseline is something
 * people do accurately, and comparing angles is not — and the figure is right
 * there either way.
 *
 * ONE HUE on a named surface, not eight.
 *
 * Off a surface this still walks the categorical palette by row, which is what
 * it has always done. On a surface it does not, and the difference is not
 * taste. These rows are sorted by the value the bar length already shows, so a
 * hue per row spends the identity channel re-encoding the one thing the reader
 * can already see — and worse, identity here would be attached to RANK rather
 * than to the package: change the range from 30d to 7d, the order shifts, and
 * a package the operator had learned as "the orange one" is now green while
 * orange belongs to something else. A colour that moves when nothing about the
 * thing moved is a colour that was never carrying meaning.
 *
 * So every bar is the surface's azure, lit along its length by the same
 * gradient — see SURFACES.ledger.markSheen for why that is decoration and not
 * a second encoding.
 */
function RankedBars({ rows, labelKey, valueKey, meta, surface }) {
  const theme = SURFACES[surface] || null;

  if (!rows?.length) {
    return (
      <p className="py-6 text-sm text-slate-500" style={theme ? { color: theme.ink } : undefined}>
        Nothing in this period.
      </p>
    );
  }
  const max = Math.max(...rows.map((r) => r[valueKey])) || 1;

  return (
    <ul className="space-y-3">
      {rows.slice(0, 8).map((r, i) => (
        <li key={r[labelKey] ?? i}>
          <div className="flex items-baseline justify-between gap-3">
            <span
              className="text-sm text-slate-200 capitalize truncate"
              style={theme ? { color: theme.inkStrong } : undefined}
            >
              {r[labelKey]}
            </span>
            <span className="text-sm font-semibold text-white tabular-nums whitespace-nowrap">
              {KES(r[valueKey])}
            </span>
          </div>
          {/* A white lift for the track, not a slate fill, which goes muddy
              over a coloured ground. */}
          <div
            className="mt-1.5 h-1.5 rounded-full bg-white/5 overflow-hidden"
            style={theme ? { backgroundColor: theme.raise } : undefined}
          >
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max((r[valueKey] / max) * 100, 1)}%`,
                background: theme ? theme.markSheen : seriesColor(i),
              }}
            />
          </div>
          {meta && (
            <p
              className="text-xs text-slate-500 mt-1"
              style={theme ? { color: theme.ink } : undefined}
            >
              {meta(r)}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

const TONES = {
  good: "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
  warning: "border-amber-500/25 bg-amber-500/10 text-amber-300",
  info: "border-blue-500/25 bg-blue-500/10 text-blue-300",
  critical: "border-red-500/25 bg-red-500/10 text-red-300",
};

function AtRisk({ label, icon: Icon, tone, bucket }) {
  return (
    <div className={`rounded-lg border p-3 text-center ${TONES[tone]}`}>
      <Icon size={14} className="mx-auto mb-1" aria-hidden="true" />
      <p className="text-xl font-bold tabular-nums">{num(bucket.count)}</p>
      <p className="text-[11px] opacity-80">{label}</p>
      <p className="text-[11px] opacity-70 mt-0.5">{KES(bucket.value)}</p>
    </div>
  );
}

function Flow({ label, icon: Icon, tone, count, value }) {
  return (
    <div className={`rounded-lg border p-4 text-center ${TONES[tone]}`}>
      <Icon size={15} className="mx-auto mb-1.5" aria-hidden="true" />
      <p className="text-2xl font-bold tabular-nums">{num(count)}</p>
      <p className="text-xs opacity-80">{label}</p>
      <p className="text-xs opacity-70 mt-0.5">{KES(value)}</p>
    </div>
  );
}
