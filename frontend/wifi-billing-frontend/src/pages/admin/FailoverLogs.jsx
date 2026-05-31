import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchFailoverLogs } from "../../services/failover";

export default function FailoverLogs() {
  const [logs, setLogs]       = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchFailoverLogs()
      .then(setLogs)
      .finally(() => setLoading(false));
  }, []);

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Failover Logs</h1>
          <p className="text-slate-500 text-sm mt-1">History of automatic and manual router migrations</p>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {["Customer", "Phone", "From Router", "To Router", "Reason", "Time"].map((h) => (
                  <th key={h} className="px-6 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="6" className="px-6 py-10 text-center text-slate-400 text-sm">Loading…</td></tr>
              ) : logs.length === 0 ? (
                <tr><td colSpan="6" className="px-6 py-10 text-center text-slate-400 text-sm">No failover events recorded</td></tr>
              ) : (
                logs.map((l) => (
                  <tr key={l.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-4 font-medium text-slate-800">{l.customer}</td>
                    <td className="px-6 py-4 text-slate-600">{l.phone}</td>
                    <td className="px-6 py-4 text-slate-500 text-xs font-mono">{l.from_router || "—"}</td>
                    <td className="px-6 py-4 font-semibold text-slate-800 text-xs font-mono">{l.to_router}</td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                        l.reason === "auto_failover"
                          ? "bg-red-50 text-red-700 border border-red-200"
                          : "bg-blue-50 text-blue-700 border border-blue-200"
                      }`}>
                        {l.reason === "auto_failover" ? "Auto Failover" : "Manual"}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-400 text-xs">{new Date(l.created_at).toLocaleString()}</td>
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
