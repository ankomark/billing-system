import { useQuery } from "@tanstack/react-query";
import { RefreshCw, ShieldAlert } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Skeleton } from "../../components/ui/Skeleton";
import EmptyState from "../../components/ui/EmptyState";
import api from "../../services/api";

function AlertRow({ a }) {
  const critical = a.percent >= 100;
  const pct = Math.min(a.percent, 100);

  return (
    <div className={`bg-white rounded-xl border p-4 ${critical ? "border-red-200" : "border-amber-200"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold text-slate-800 truncate">{a.customer}</p>
          <p className="text-sm text-slate-500">{a.phone}</p>
        </div>
        <div className="text-right flex-shrink-0">
          <p className={`text-lg font-bold ${critical ? "text-red-600" : "text-amber-600"}`}>
            {a.percent}%
          </p>
          <p className="text-xs text-slate-500">{a.used_gb} GB / {a.cap_gb} GB</p>
        </div>
      </div>
      <div className="mt-3">
        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${critical ? "bg-red-500" : "bg-amber-400"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </div>
  );
}

function SkeletonAlert() {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 space-y-3">
      <div className="flex justify-between">
        <div className="space-y-1.5">
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-3 w-24" />
        </div>
        <Skeleton className="h-7 w-12" />
      </div>
      <Skeleton className="h-2 w-full rounded-full" />
    </div>
  );
}

export default function UsageAlerts() {
  const { data: alerts = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["usage-alerts"],
    queryFn: () =>
      api.get("admin/usage/Alerts/").then((r) => r.data || []),
    staleTime: 2 * 60 * 1000,
  });

  const critical = alerts.filter((a) => a.percent >= 100).length;

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Usage Alerts</h1>
            <p className="text-slate-500 text-sm mt-1">
              Customers at 80%+ of their monthly data cap
            </p>
            {!isLoading && alerts.length > 0 && (
              <div className="flex gap-3 mt-2">
                <span className="text-xs font-semibold text-red-600 bg-red-50 border border-red-200 px-2.5 py-0.5 rounded-full">
                  {critical} over limit
                </span>
                <span className="text-xs font-semibold text-amber-600 bg-amber-50 border border-amber-200 px-2.5 py-0.5 rounded-full">
                  {alerts.length - critical} nearing limit
                </span>
              </div>
            )}
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {isError && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-4 text-red-700 text-sm">
            Failed to load usage alerts. Try refreshing.
          </div>
        )}

        {isLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => <SkeletonAlert key={i} />)}
          </div>
        ) : alerts.length === 0 ? (
          <div className="bg-white rounded-xl border border-slate-200">
            <EmptyState
              icon={<ShieldAlert size={24} />}
              title="All customers within limits"
              description="No customers are approaching their monthly data cap."
            />
          </div>
        ) : (
          <div className="space-y-3">
            {alerts.map((a, i) => <AlertRow key={i} a={a} />)}
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
