import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, Download } from "lucide-react";
import { Card, CardHeader, DataTable, KES, num } from "./ui";
import { fetchMonthlyRevenue } from "../../services/dashboard";

const monthName = (ym, style = "long") => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-KE", { month: style, year: "numeric" });
};

/**
 * Earnings month by month.
 *
 * The dashboard used to have no memory: at midnight on the 1st, "This month"
 * went back to zero and the month that had just closed was nowhere on the
 * page. This is the record of every month, the one in progress at the top.
 *
 * Change is against the month before; for the month in progress it is against
 * the same days of last month, so the 5th is not compared with a whole month
 * and always shown as a fall.
 */
export default function MonthlyEarnings() {
  const [months, setMonths] = useState(6);
  const { data: rows, isLoading, isError } = useQuery({
    queryKey: ["monthly-revenue", months],
    queryFn: () => fetchMonthlyRevenue(months),
    staleTime: 5 * 60 * 1000,
    placeholderData: (prev) => prev,
  });

  const columns = [
    {
      key: "month",
      label: "Month",
      className: "font-medium text-slate-100 whitespace-nowrap",
      render: (r) => (
        <>
          {monthName(r.month)}
          {r.in_progress && (
            <span className="ml-2 rounded bg-blue-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-blue-300">
              so far
            </span>
          )}
        </>
      ),
    },
    {
      key: "revenue",
      label: "Earnings",
      align: "right",
      className: "font-semibold text-white whitespace-nowrap tabular-nums",
      render: (r) => KES(r.revenue),
    },
    {
      key: "change",
      label: "Change",
      align: "right",
      className: "whitespace-nowrap tabular-nums",
      render: (r) => <Change value={r.change} against={r.change_against} />,
    },
    {
      key: "payments",
      label: "Payments",
      align: "right",
      hideBelow: "sm",
      className: "tabular-nums text-slate-300",
      render: (r) => num(r.payments),
    },
    {
      key: "customers",
      label: "Paying customers",
      align: "right",
      hideBelow: "md",
      className: "tabular-nums text-slate-300",
      render: (r) => num(r.customers),
    },
    {
      key: "mpesa",
      label: "M-Pesa",
      align: "right",
      hideBelow: "lg",
      className: "tabular-nums text-slate-400 whitespace-nowrap",
      render: (r) => KES(r.by_method?.mpesa),
    },
    {
      key: "other",
      label: "Cash / bank",
      align: "right",
      hideBelow: "lg",
      className: "tabular-nums text-slate-400 whitespace-nowrap",
      render: (r) => KES((r.by_method?.cash || 0) + (r.by_method?.bank || 0)),
    },
    {
      key: "comps",
      label: "Free",
      align: "right",
      hideBelow: "lg",
      className: "tabular-nums text-slate-500",
      render: (r) => num(r.comps),
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Monthly earnings"
        subtitle="Every month's takings, including the ones that have closed"
        chip="green"
        icon={CalendarDays}
        action={
          <div className="flex items-center gap-2">
            <select
              value={months}
              onChange={(e) => setMonths(Number(e.target.value))}
              aria-label="How many months to show"
              className="rounded-lg border border-white/15 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value={6}>6 months</option>
              <option value={12}>12 months</option>
              <option value={24}>24 months</option>
            </select>
            <button
              type="button"
              onClick={() => downloadCsv(rows)}
              disabled={!rows?.length}
              title="Download as a spreadsheet (CSV)"
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-2.5 py-1.5 text-xs font-medium text-slate-300 hover:bg-white/5 disabled:opacity-50"
            >
              <Download size={12} aria-hidden="true" /> CSV
            </button>
          </div>
        }
      />
      {isError ? (
        <p className="px-1 py-6 text-sm text-red-300">Couldn't load monthly earnings.</p>
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.month}
          loading={isLoading}
          loadingRows={6}
          dense
          empty="No earnings recorded yet."
        />
      )}
    </Card>
  );
}

function Change({ value, against }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-500" title="Nothing to compare with">—</span>;
  }
  const up = value > 0;
  const flat = value === 0;
  const cls = flat ? "text-slate-400" : up ? "text-emerald-400" : "text-red-300";
  return (
    <span className={cls} title={`Compared with the ${against}`}>
      {flat ? "" : up ? "▲ " : "▼ "}
      {Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 1 })}%
    </span>
  );
}

/** The table as a spreadsheet, for the books. Built here: it is already loaded. */
function downloadCsv(rows) {
  if (!rows?.length) return;
  const header = ["Month", "Earnings (KES)", "Change %", "Payments", "Paying customers",
    "M-Pesa (KES)", "Cash (KES)", "Bank (KES)", "Free accesses", "Note"];
  const lines = rows.map((r) => [
    r.month,
    r.revenue,
    r.change ?? "",
    r.payments,
    r.customers,
    r.by_method?.mpesa || 0,
    r.by_method?.cash || 0,
    r.by_method?.bank || 0,
    r.comps,
    r.in_progress ? "month in progress" : "",
  ]);
  const csv = [header, ...lines]
    .map((cols) => cols.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(","))
    .join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `monthly-earnings-${rows[0].month}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}
