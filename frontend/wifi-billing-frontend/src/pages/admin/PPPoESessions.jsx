import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchAdminPPPoESessions } from "../../services/pppoe";
import api from "../../services/api";

function fmtMB(bytes) {
  return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

function fmtUptime(raw) {
  const seconds = parseInt(raw) || 0;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function PPPoESessions() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState({});

  const loadSessions = async () => {
    try {
      const data = await fetchAdminPPPoESessions();
      setSessions(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSessions();
    const interval = setInterval(loadSessions, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleDisconnect = async (username) => {
    if (!window.confirm(`Disconnect ${username}?`)) return;
    setDisconnecting((p) => ({ ...p, [username]: true }));
    try {
      await api.post("/admin/pppoe/disconnect/", { username });
      await loadSessions();
    } catch {
      alert(`Failed to disconnect ${username}`);
    } finally {
      setDisconnecting((p) => ({ ...p, [username]: false }));
    }
  };

  const totalBytes = sessions.reduce((s, x) => s + x.rx_bytes + x.tx_bytes, 0);

  return (
    <AdminLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">PPPoE Sessions</h1>
            <p className="text-slate-500 text-sm mt-1">Real-time active connections — auto-refreshes every 10s</p>
          </div>
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            {sessions.length} active
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { label: "Active Sessions", value: sessions.length },
            { label: "Total Bandwidth", value: fmtMB(totalBytes) },
            { label: "Active Routers", value: [...new Set(sessions.map((s) => s.router))].length },
            { label: "Auto-refresh", value: "10s" },
          ].map((card) => (
            <div key={card.label} className="bg-white rounded-xl border border-slate-200 p-4">
              <p className="text-xs text-slate-500 font-medium">{card.label}</p>
              <p className="text-2xl font-bold text-slate-800 mt-1">{card.value}</p>
            </div>
          ))}
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          {loading ? (
            <div className="p-12 text-center text-slate-400 text-sm">Loading sessions…</div>
          ) : sessions.length === 0 ? (
            <div className="p-12 text-center">
              <p className="text-2xl mb-2">📡</p>
              <p className="text-slate-600 font-medium">No active PPPoE sessions</p>
              <p className="text-slate-400 text-sm mt-1">Waiting for connections…</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 border-b border-slate-200">
                  <tr>
                    {["Customer", "Username", "IP Address", "Uptime", "Download", "Upload", "Router", "Action"].map((h) => (
                      <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s, i) => (
                    <tr key={i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors">
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 bg-blue-100 text-blue-700 rounded-lg flex items-center justify-center font-bold text-xs flex-shrink-0">
                            {(s.customer || "?").charAt(0).toUpperCase()}
                          </div>
                          <span className="font-medium text-slate-800">{s.customer || "Unknown"}</span>
                        </div>
                      </td>
                      <td className="px-5 py-3.5">
                        <code className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded text-xs">{s.username}</code>
                      </td>
                      <td className="px-5 py-3.5 text-slate-600 font-mono text-xs">{s.ip_address}</td>
                      <td className="px-5 py-3.5 text-slate-700 font-medium">{fmtUptime(s.uptime)}</td>
                      <td className="px-5 py-3.5 text-blue-700 font-medium">{fmtMB(s.rx_bytes)}</td>
                      <td className="px-5 py-3.5 text-emerald-700 font-medium">{fmtMB(s.tx_bytes)}</td>
                      <td className="px-5 py-3.5">
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-700">
                          {s.router}
                        </span>
                      </td>
                      <td className="px-5 py-3.5">
                        <button
                          onClick={() => handleDisconnect(s.username)}
                          disabled={disconnecting[s.username]}
                          className="bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded-md text-xs font-semibold disabled:opacity-50 transition-colors"
                        >
                          {disconnecting[s.username] ? "…" : "Disconnect"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </AdminLayout>
  );
}
