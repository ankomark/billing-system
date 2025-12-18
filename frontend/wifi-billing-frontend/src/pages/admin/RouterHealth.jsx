import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchRouterHealth, fetchFailoverLogs } from "../../services/routers";

export default function RouterHealth() {
  const [routers, setRouters] = useState([]);
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    const load = async () => {
      setRouters(await fetchRouterHealth());
      setLogs(await fetchFailoverLogs());
    };
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <AdminLayout>
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Routers Health</h1>

        <div className="bg-white rounded shadow overflow-x-auto mb-8">
          <table className="w-full text-sm">
            <thead className="bg-gray-100">
              <tr>
                <th className="p-3 text-left">Name</th>
                <th className="p-3 text-left">IP</th>
                <th className="p-3 text-left">Priority</th>
                <th className="p-3 text-left">Status</th>
                <th className="p-3 text-left">Last Seen</th>
                <th className="p-3 text-left">Last Error</th>
              </tr>
            </thead>
            <tbody>
              {routers.map((r) => (
                <tr key={r.id} className="border-t">
                  <td className="p-3">{r.name}</td>
                  <td className="p-3">{r.ip_address}:{r.api_port}</td>
                  <td className="p-3">{r.priority}</td>
                  <td className="p-3">
                    <span className={`px-2 py-1 rounded text-xs ${r.is_online ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                      {r.is_online ? "ONLINE" : "OFFLINE"}
                    </span>
                  </td>
                  <td className="p-3">{r.last_seen ? new Date(r.last_seen).toLocaleString() : "-"}</td>
                  <td className="p-3 text-red-700">{r.last_error || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h2 className="text-xl font-bold mb-3">Recent Failovers</h2>
        <div className="bg-white rounded shadow overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-100">
              <tr>
                <th className="p-3 text-left">Customer</th>
                <th className="p-3 text-left">Phone</th>
                <th className="p-3 text-left">From</th>
                <th className="p-3 text-left">To</th>
                <th className="p-3 text-left">Reason</th>
                <th className="p-3 text-left">Time</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((l, i) => (
                <tr key={i} className="border-t">
                  <td className="p-3">{l.customer}</td>
                  <td className="p-3">{l.phone}</td>
                  <td className="p-3">{l.from_router || "-"}</td>
                  <td className="p-3">{l.to_router || "-"}</td>
                  <td className="p-3">{l.reason}</td>
                  <td className="p-3">{new Date(l.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </AdminLayout>
  );
}
