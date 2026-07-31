import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { fetchRouters } from "../../services/routers";

export default function Routers() {
  const { data: routers = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["routers"],
    queryFn: fetchRouters,
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000,
  });

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Routers</h1>
            <p className="text-slate-400 text-sm mt-1">
              Monitor router availability, priority, and network status
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
            Failed to load routers. Try refreshing.
          </div>
        )}

        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-white/5 border-b border-white/10">
              <tr>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Name</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">IP Address</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Station</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Priority</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {isLoading ? (
                <SkeletonTable rows={4} cols={5} />
              ) : routers.length === 0 ? (
                <tr>
                  <td colSpan="5" className="px-6 py-10 text-center text-slate-500 text-sm">
                    No routers configured
                  </td>
                </tr>
              ) : (
                routers.map((router) => (
                  <tr key={router.id} className="hover:bg-white/5 transition-colors">
                    <td className="px-6 py-4 font-medium text-white">{router.name}</td>
                    <td className="px-6 py-4 font-mono text-xs text-slate-300">{router.ip_address}</td>
                    <td className="px-6 py-4 text-slate-300">
                      {/* Blank is the normal single-site case, not missing data. */}
                      {router.station_name || <span className="text-slate-500">—</span>}
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-blue-500/10 text-blue-300 border border-blue-500/30">
                        Priority {router.priority}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      {router.is_online ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/100"></span>
                          Online
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-red-500/10 text-red-300 border border-red-500/30">
                          <span className="w-1.5 h-1.5 rounded-full bg-red-500/100"></span>
                          Offline
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </AdminLayout>
  );
}
