import { useQuery } from "@tanstack/react-query";
import { RefreshCw, AlertTriangle } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import AdminUsageGraph from "../../components/usage/AdminUsageGraph";
import { SkeletonCard } from "../../components/ui/Skeleton";
import { fetchDashboardSummary } from "../../services/dashboard";

const STALE = 60 * 1000; // 1 minute

export default function Dashboard() {
  const { data, isLoading, isError, error, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["dashboard"],
    queryFn: fetchDashboardSummary,
    staleTime: STALE,
  });

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" })
    : null;

  if (isError) {
    return (
      <AdminLayout>
        <div className="flex flex-col items-center justify-center h-64 gap-4">
          <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center">
            <AlertTriangle size={22} className="text-red-600" />
          </div>
          <div className="text-center">
            <p className="font-semibold text-slate-800">Failed to load dashboard</p>
            <p className="text-slate-500 text-sm mt-1">
              {error?.response?.data?.detail || "Check your connection and try again."}
            </p>
          </div>
          <button
            onClick={() => refetch()}
            className="inline-flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            <RefreshCw size={14} />
            Retry
          </button>
        </div>
      </AdminLayout>
    );
  }

  const rev   = data?.revenue_summary;
  const stats = data?.customer_stats;

  return (
    <AdminLayout>
      <div className="space-y-6">
        {/* Page title */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Dashboard</h1>
            <p className="text-slate-500 text-sm mt-1">Overview of your ISP billing system</p>
          </div>
          <div className="flex items-center gap-3">
            {lastUpdated && (
              <span className="text-xs text-slate-400 hidden sm:block">
                Updated {lastUpdated}
              </span>
            )}
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {/* Revenue */}
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Revenue</p>
          {isLoading ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <SkeletonCard /><SkeletonCard /><SkeletonCard />
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <StatCard label="Today"      value={`KES ${rev?.today ?? 0}`}      color="blue" />
              <StatCard label="This Month" value={`KES ${rev?.this_month ?? 0}`} color="emerald" />
              <StatCard label="This Year"  value={`KES ${rev?.this_year ?? 0}`}  color="violet" />
            </div>
          )}
        </div>

        {/* Subscriptions */}
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
            Subscriptions & Invoices
          </p>
          {isLoading ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatCard label="Active"  value={stats?.active_subscriptions ?? 0}  color="emerald" small />
              <StatCard label="Expired" value={stats?.expired_subscriptions ?? 0} color="red"     small />
              <StatCard label="Unpaid"  value={stats?.unpaid_invoices ?? 0}        color="amber"   small />
              <StatCard label="Pending" value={stats?.pending_invoices ?? 0}       color="blue"    small />
            </div>
          )}
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
      <p className={`font-bold mt-1 ${small ? "text-2xl" : "text-3xl"} ${c.text}`}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </p>
    </div>
  );
}
