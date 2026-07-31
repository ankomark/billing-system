import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { fetchRouterHealth, fetchFailoverLogs, fetchRouterEvents } from "../../services/routers";

export default function RouterHealth() {
  const { data: routers = [], isLoading: loadingRouters, isFetching: fetchingRouters, refetch: refetchRouters } = useQuery({
    queryKey: ["router-health"],
    queryFn: fetchRouterHealth,
    refetchInterval: 10 * 1000,
    staleTime: 10 * 1000,
  });

  // Availability and the history of going down. The table above shows current
  // state and one last_error that each new failure overwrites, so it can never
  // answer "has this been flapping?" or "how long were we down?".
  const { data: events } = useQuery({
    queryKey: ["router-events"],
    queryFn: () => fetchRouterEvents(7),
    staleTime: 60 * 1000,
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

        {/* Per-site rollup. A router-by-router list answers "is this box up";
            an operator with two towns is asking "is Kilifi up". */}
        {events?.stations?.length > 1 && (
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
              By station — last {events.days} days
            </p>
            <div className="grid gap-4 sm:grid-cols-3">
              {events.stations.map((st) => (
                <div key={st.id ?? "none"} className="bg-white rounded-xl border border-slate-200 p-4">
                  <p className="font-medium text-slate-800">
                    {st.name || <span className="text-slate-400">No station</span>}
                  </p>
                  <p className={`text-2xl font-bold tabular-nums mt-1 ${
                    st.uptime_percent >= 99 ? "text-emerald-600"
                    : st.uptime_percent >= 95 ? "text-amber-600" : "text-red-600"
                  }`}>
                    {st.uptime_percent}%
                  </p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    {st.routers} router{st.routers === 1 ? "" : "s"}
                    {st.routers_offline > 0 && (
                      <span className="text-red-600 font-medium">
                        {" "}· {st.routers_offline} offline now
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-slate-400">
                    {st.outages} outage{st.outages === 1 ? "" : "s"} in the period
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Availability over the last week */}
        {events?.routers?.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
              Availability — last {events.days} days
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {events.routers.map((r) => (
                <div key={r.id} className="bg-white rounded-xl border border-slate-200 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-medium text-slate-800 truncate">{r.name}</p>
                      <p className="text-xs text-slate-400">
                        {r.ip_address}
                        {r.station_name && (
                          <span className="text-slate-500"> · {r.station_name}</span>
                        )}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className={`text-xl font-bold tabular-nums ${
                        r.availability.uptime_percent >= 99
                          ? "text-emerald-600"
                          : r.availability.uptime_percent >= 95
                          ? "text-amber-600"
                          : "text-red-600"
                      }`}>
                        {r.availability.uptime_percent}%
                      </p>
                      <p className="text-xs text-slate-400">
                        {r.availability.outages} outage
                        {r.availability.outages === 1 ? "" : "s"}
                        {r.availability.downtime_seconds > 0 &&
                          ` · ${formatDuration(r.availability.downtime_seconds)} down`}
                      </p>
                    </div>
                  </div>

                  {r.events.length === 0 ? (
                    <p className="text-xs text-slate-400 mt-3 pt-3 border-t border-slate-100">
                      No changes in this period.
                    </p>
                  ) : (
                    <ul className="mt-3 pt-3 border-t border-slate-100 space-y-1.5 max-h-40 overflow-y-auto">
                      {r.events.map((e, i) => (
                        <li key={i} className="text-xs flex gap-2">
                          <span
                            className={`mt-1 inline-block h-1.5 w-1.5 rounded-full flex-shrink-0 ${
                              e.kind === "came_online" ? "bg-emerald-500" : "bg-red-500"
                            }`}
                            aria-hidden="true"
                          />
                          <span className="min-w-0 flex-1">
                            <span className="text-slate-700 font-medium">
                              {e.kind === "came_online" ? "Came online" : "Went offline"}
                            </span>
                            {e.cause && (
                              <span className="text-slate-400"> · {CAUSE_LABELS[e.cause] || e.cause}</span>
                            )}
                            {e.detail && (
                              <span className="block text-slate-400 truncate">{e.detail}</span>
                            )}
                          </span>
                          <span className="text-slate-400 whitespace-nowrap">
                            {new Date(e.at).toLocaleString("en-KE", {
                              month: "short", day: "numeric",
                              hour: "2-digit", minute: "2-digit",
                            })}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

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

const CAUSE_LABELS = {
  unreachable: "could not be reached",
  auth_failed: "refused our credentials",
  error: "error",
};

/** Compact enough to sit under a percentage without wrapping. */
function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}
