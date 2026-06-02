import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { fetchRouterHealth, fetchFailoverLogs } from "../../services/routers";

export default function RouterHealth() {
  const { data: routers = [], isLoading: loadingRouters, isFetching: fetchingRouters, refetch: refetchRouters } = useQuery({
    queryKey: ["router-health"],
    queryFn: fetchRouterHealth,
    refetchInterval: 10 * 1000,
    staleTime: 10 * 1000,
  });

  const { data: logs = [], isLoading: loadingLogs } = useQuery({
    queryKey: ["failover-logs"],
    queryFn: fetchFailoverLogs,
    staleTime: 60 * 1000,
  });

  return (
    <AdminLayout>
      <div className="space-y-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Router Health</h1>
            <p className="text-slate-500 text-sm mt-1">
              Live status and recent failover events — auto-refreshes every 10s
            </p>
          </div>
          <button
            onClick={() => refetchRouters()}
            disabled={fetchingRouters}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={fetchingRouters ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {/* Router status table */}
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Router Status</p>
          <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 border-b border-slate-200">
                  <tr>
                    {["Name", "IP / Port", "Priority", "Status", "Last Seen", "Last Error"].map((h) => (
                      <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loadingRouters ? (
                    <SkeletonTable rows={3} cols={6} />
                  ) : routers.length === 0 ? (
                    <tr>
                      <td colSpan="6" className="px-5 py-10 text-center text-slate-400 text-sm">
                        No routers configured
                      </td>
                    </tr>
                  ) : (
                    routers.map((r) => (
                      <tr key={r.id} className="hover:bg-slate-50 transition-colors">
                        <td className="px-5 py-3.5 font-medium text-slate-800">{r.name}</td>
                        <td className="px-5 py-3.5 font-mono text-xs text-slate-600">
                          {r.ip_address}:{r.api_port}
                        </td>
                        <td className="px-5 py-3.5">
                          <span className="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-blue-50 text-blue-700 border border-blue-100">
                            {r.priority}
                          </span>
                        </td>
                        <td className="px-5 py-3.5">
                          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${
                            r.is_online
                              ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                              : "bg-red-50 text-red-700 border border-red-200"
                          }`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${r.is_online ? "bg-emerald-500" : "bg-red-500"}`} />
                            {r.is_online ? "Online" : "Offline"}
                          </span>
                        </td>
                        <td className="px-5 py-3.5 text-slate-500 text-xs whitespace-nowrap">
                          {r.last_seen ? new Date(r.last_seen).toLocaleString("en-KE") : "—"}
                        </td>
                        <td className="px-5 py-3.5 text-red-600 text-xs">{r.last_error || "—"}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Recent failovers */}
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Recent Failovers</p>
          <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 border-b border-slate-200">
                  <tr>
                    {["Customer", "Phone", "From", "To", "Reason", "Time"].map((h) => (
                      <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loadingLogs ? (
                    <SkeletonTable rows={4} cols={6} />
                  ) : logs.length === 0 ? (
                    <tr>
                      <td colSpan="6" className="px-5 py-10 text-center text-slate-400 text-sm">
                        No failover events recorded
                      </td>
                    </tr>
                  ) : (
                    logs.map((l, i) => (
                      <tr key={i} className="hover:bg-slate-50 transition-colors">
                        <td className="px-5 py-3.5 font-medium text-slate-800">{l.customer}</td>
                        <td className="px-5 py-3.5 text-slate-600">{l.phone}</td>
                        <td className="px-5 py-3.5 text-slate-500 text-xs font-mono">{l.from_router || "—"}</td>
                        <td className="px-5 py-3.5 font-semibold text-slate-800 text-xs font-mono">{l.to_router}</td>
                        <td className="px-5 py-3.5">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                            l.reason === "auto_failover"
                              ? "bg-red-50 text-red-700 border border-red-200"
                              : "bg-blue-50 text-blue-700 border border-blue-200"
                          }`}>
                            {l.reason === "auto_failover" ? "Auto Failover" : "Manual"}
                          </span>
                        </td>
                        <td className="px-5 py-3.5 text-slate-400 text-xs whitespace-nowrap">
                          {new Date(l.created_at).toLocaleString("en-KE")}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </AdminLayout>
  );
}
