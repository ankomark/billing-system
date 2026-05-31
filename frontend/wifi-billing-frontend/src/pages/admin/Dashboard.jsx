import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchDashboardSummary } from "../../services/dashboard";
import AdminUsageGraph from "../../components/usage/AdminUsageGraph";

export default function Dashboard() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetchDashboardSummary().then(setData);
  }, []);

  if (!data) {
    return (
      <AdminLayout>
        <div className="flex items-center justify-center h-48 text-slate-400 text-sm">
          Loading dashboard…
        </div>
      </AdminLayout>
    );
  }

  const { revenue_summary: rev, customer_stats: stats } = data;

  return (
    <AdminLayout>
      <div className="space-y-6">
        {/* Page title */}
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Dashboard</h1>
          <p className="text-slate-500 text-sm mt-1">Overview of your ISP billing system</p>
        </div>

        {/* Revenue */}
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Revenue</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <StatCard label="Today"      value={`KES ${rev.today}`}      color="blue" />
            <StatCard label="This Month" value={`KES ${rev.this_month}`} color="emerald" />
            <StatCard label="This Year"  value={`KES ${rev.this_year}`}  color="violet" />
          </div>
        </div>

        {/* Subscriptions */}
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Subscriptions & Invoices</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatCard label="Active"    value={stats.active_subscriptions}  color="emerald" small />
            <StatCard label="Expired"   value={stats.expired_subscriptions} color="red"     small />
            <StatCard label="Unpaid"    value={stats.unpaid_invoices}       color="amber"   small />
            <StatCard label="Pending"   value={stats.pending_invoices}      color="blue"    small />
          </div>
        </div>

        {/* Usage graph */}
        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <p className="text-sm font-semibold text-slate-700 mb-4">Network Usage (7 days)</p>
          <AdminUsageGraph />
        </div>
      </div>
    </AdminLayout>
  );
}

const colorMap = {
  blue:    { bg: "bg-blue-50",    text: "text-blue-700",    border: "border-blue-100"    },
  emerald: { bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-100" },
  violet:  { bg: "bg-violet-50",  text: "text-violet-700",  border: "border-violet-100"  },
  amber:   { bg: "bg-amber-50",   text: "text-amber-700",   border: "border-amber-100"   },
  red:     { bg: "bg-red-50",     text: "text-red-700",     border: "border-red-100"     },
};

function StatCard({ label, value, color = "blue", small }) {
  const c = colorMap[color] || colorMap.blue;
  return (
    <div className={`bg-white rounded-xl border ${c.border} p-4 shadow-sm`}>
      <p className="text-xs text-slate-500 font-medium">{label}</p>
      <p className={`font-bold mt-1 ${small ? "text-2xl" : "text-3xl"} ${c.text}`}>{value}</p>
    </div>
  );
}
