import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { fetchFailoverLogs } from "../../services/failover";

export default function FailoverLogs() {
  const { data: logs = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["failover-logs"],
    queryFn: fetchFailoverLogs,
    staleTime: 60 * 1000,
  });

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Failover Logs</h1>
            <p className="text-slate-400 text-sm mt-1">
              History of automatic and manual router migrations
            </p>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-white/15 rounded-lg text-xs font-medium text-slate-300 hover:bg-white/5 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {isError && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-300 text-sm">
            Failed to load failover logs. Try refreshing.
          </div>
        )}

        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-white/5 border-b border-white/10">
                <tr>
                  {["Customer", "Phone", "From Router", "To Router", "Reason", "Time"].map((h) => (
                    <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {isLoading ? (
                  <SkeletonTable rows={6} cols={6} />
                ) : logs.length === 0 ? (
                  <tr>
                    <td colSpan="6" className="px-6 py-10 text-center text-slate-500 text-sm">
                      No failover events recorded
                    </td>
                  </tr>
                ) : (
                  logs.map((l) => (
                    <tr key={l.id} className="hover:bg-white/5 transition-colors">
                      <td className="px-6 py-4 font-medium text-white">{l.customer}</td>
                      <td className="px-6 py-4 text-slate-300">{l.phone}</td>
                      <td className="px-6 py-4 text-slate-400 text-xs font-mono">{l.from_router || "—"}</td>
                      <td className="px-6 py-4 font-semibold text-white text-xs font-mono">{l.to_router}</td>
                      <td className="px-6 py-4">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                          l.reason === "auto_failover"
                            ? "bg-red-500/10 text-red-300 border border-red-500/30"
                            : "bg-blue-500/10 text-blue-300 border border-blue-500/30"
                        }`}>
                          {l.reason === "auto_failover" ? "Auto Failover" : "Manual"}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-slate-500 text-xs whitespace-nowrap">
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
    </AdminLayout>
  );
}
