import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import { Card, CardHeader, DataTable, KES, num, seriesColor } from "./ui";

/**
 * What has sold since midnight, package by package.
 *
 * The panels below this one answer "how is the month going". This answers "how
 * is today going", which is a different question and the one actually asked at
 * 11am with three hundred people on the network and takings that look thin.
 *
 * Concurrency does not answer it and is the thing most likely to mislead: on
 * 2026-09-13, 247 people were connected and only 81 had bought anything that
 * day. The rest were on weekly and monthly packages purchased earlier and owed
 * nothing. A busy network and a quiet till are the normal state of an ISP
 * selling multi-day bundles, and this panel is what makes that legible instead
 * of alarming.
 *
 * The table is not decoration beside the chart. Several of the palette's slots
 * fall below 3:1 against this surface, and the rule for that is the numbers
 * visible somewhere -- which is also why the table carries the share, rather
 * than leaving a reader to estimate it off the wedges.
 */
export default function TodaySales({ today }) {
  if (!today) return null;

  const packages = today.packages ?? [];
  const asOf = today.as_of
    ? new Date(today.as_of).toLocaleTimeString("en-KE", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  // Only packages that actually earned get a wedge. A comp sells at zero and
  // belongs in the table -- free internet given away is still a package
  // issued -- but a zero-width slice is an invisible lie in a pie.
  const wedges = packages.filter((p) => p.revenue > 0);

  const columns = [
    {
      key: "name",
      label: "Package",
      render: (r) => (
        <span className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ background: colourFor(r, wedges) }}
          />
          <span className="truncate">{r.name}</span>
        </span>
      ),
    },
    {
      key: "purchases",
      label: "Sold",
      align: "right",
      render: (r) => num(r.purchases),
    },
    {
      key: "customers",
      label: "Buyers",
      align: "right",
      render: (r) => num(r.customers),
    },
    {
      // Beside the buyers, because that is the comparison being made: of the
      // people who got this package today, how many paid for it. A count on
      // its own answers the smaller half -- three free weeks could be three
      // apologies for an outage or three people let on for nothing -- so the
      // operator's own word from the counter form comes with it.
      key: "comps",
      label: "Given",
      align: "right",
      render: (r) =>
        r.comps ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="tabular-nums">{num(r.comps)}</span>
            <span
              className="max-w-[11rem] truncate text-xs text-slate-400"
              title={r.comp_reasons.map((x) => `${x.reason} ×${x.count}`).join(", ")}
            >
              {reasonSummary(r.comp_reasons)}
            </span>
          </span>
        ) : (
          <span className="text-slate-600">—</span>
        ),
    },
    {
      key: "revenue",
      label: "Revenue",
      align: "right",
      render: (r) => KES(r.revenue),
    },
    {
      key: "share",
      label: "Share",
      align: "right",
      render: (r) => (r.revenue > 0 ? `${r.share}%` : "—"),
    },
  ];

  return (
    <Card padded={false}>
      <CardHeader
        title="Today"
        subtitle={
          asOf
            ? `Since midnight, as of ${asOf} — resets at midnight`
            : "Since midnight — resets at midnight"
        }
      />

      <div className="grid gap-4 px-5 pb-5 pt-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div>
          <div className="mb-4 flex flex-wrap gap-6">
            <Figure label="Revenue today" value={KES(today.revenue ?? 0)} big />
            <Figure label="Vouchers sold" value={num(today.purchases ?? 0)} />
            <Figure label="Buyers" value={num(today.customers ?? 0)} />
            {today.comps > 0 && (
              <Figure label="Given free" value={num(today.comps)} />
            )}
          </div>

          {/* Why a full network can sit above a quiet till.
              The sentence this panel exists for: on 13 September there were
              341 sessions against KSh 1,865 by 10:54, which reads as a
              collapse until you can see that most of those people bought on
              earlier days and owe nothing today. */}
          {(today.sessions != null || today.covered > 0) && (
            <p className="mb-4 text-xs text-slate-400">
              {today.sessions != null && (
                <>
                  <span className="tabular-nums text-slate-300">
                    {num(today.sessions)}
                  </span>{" "}
                  sessions
                  <span className="mx-2 text-slate-600">·</span>
                </>
              )}
              <span className="tabular-nums text-slate-300">
                {num(today.covered ?? 0)}
              </span>{" "}
              holding a live package
              <span className="mx-2 text-slate-600">·</span>
              <span className="tabular-nums text-slate-300">
                {num(today.bought_today ?? 0)}
              </span>{" "}
              bought today
            </p>
          )}

          <DataTable
            columns={columns}
            rows={packages}
            rowKey={(r) => r.name}
            dense
            empty="Nothing sold yet today"
          />
        </div>

        {wedges.length > 0 && (
          <div className="min-h-[220px]">
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={wedges}
                  dataKey="revenue"
                  nameKey="name"
                  innerRadius={52}
                  outerRadius={86}
                  paddingAngle={2}
                  stroke="none"
                  // Off, deliberately. Package names are long enough that the
                  // labels overlap at this size and the reader ends up
                  // deciphering the chart instead of reading the table beside
                  // it, which carries the same numbers exactly.
                  isAnimationActive={false}
                >
                  {wedges.map((p, i) => (
                    <Cell key={p.name} fill={seriesColor(i)} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "rgba(15,23,42,0.95)",
                    border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: 10,
                    fontSize: 12,
                  }}
                  formatter={(value, name) => [KES(value), name]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}

/**
 * The reasons, in the space a table cell has.
 *
 * One reason reads as itself. Several are summarised rather than run together,
 * because a cell wide enough for "Refund, Router fail, trial, testing" is a
 * cell that pushes the revenue column off a laptop screen. The full list is on
 * the title, which is where a reader who wants it will look.
 */
function reasonSummary(reasons) {
  if (!reasons?.length) return null;
  const [first, ...rest] = reasons;
  return rest.length ? `${first.reason} +${rest.length}` : first.reason;
}

/** The wedge colours and the table dots have to be the same, or the dot is a lie. */
function colourFor(row, wedges) {
  const i = wedges.findIndex((w) => w.name === row.name);
  return i === -1 ? "rgba(148,163,184,0.45)" : seriesColor(i);
}

function Figure({ label, value, big = false }) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        {label}
      </p>
      <p
        className={`mt-0.5 font-semibold text-slate-100 ${
          big ? "text-2xl" : "text-lg"
        }`}
      >
        {value}
      </p>
    </div>
  );
}
