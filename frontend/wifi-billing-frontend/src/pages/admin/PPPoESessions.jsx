import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useConfirm } from "../../components/ui/ConfirmModal";
import { fetchAdminPPPoESessions } from "../../services/pppoe";
import api from "../../services/api";

function fmtMB(bytes) {
  return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

// Seconds from a RouterOS duration, which is a string and not a number.
//
// This read `parseInt(raw)`, and parseInt stops at the first character that is
// not a digit -- so "1h17m17s" came back as 1, "15m" as 15, and "2d3h4m5s" as
// 2. Every one of those is under a minute once treated as seconds, so the
// column showed 0m for every session on the page, always. The only uptimes it
// could have rendered are ones RouterOS does not emit: it writes 90 minutes as
// "1h30m", never "90m".
//
// RouterOS drops any unit that is zero, so every part here is optional. Some
// builds report a bare integer of seconds or an "h:mm:ss" clock instead, and
// both are accepted rather than read as zero. billing/router_service.py has
// the same parser on the Python side, for the same reason.
export function uptimeSeconds(raw) {
  const text = String(raw ?? "").trim().toLowerCase();
  if (!text) return 0;

  if (/^\d+$/.test(text)) return parseInt(text, 10);

  if (text.includes(":")) {
    const parts = text.split(":");
    if (parts.length > 3 || !parts.every((p) => /^\d+$/.test(p))) return 0;
    return parts.reduce((total, p) => total * 60 + parseInt(p, 10), 0);
  }

  const m = text.match(
    /^(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/
  );
  if (!m) return 0;

  const [w, d, h, min, s] = m.slice(1).map((v) => parseInt(v || "0", 10));
  return w * 604800 + d * 86400 + h * 3600 + min * 60 + s;
}

function fmtUptime(raw) {
  const seconds = uptimeSeconds(raw);
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  // Seconds matter at this end of the scale: a session that has just come up
  // is the interesting case on this page, and rounding it to "0m" is what
  // made a working reconnect look like a dead one.
  if (m > 0) return `${m}m`;
  return `${seconds}s`;
}

export default function PPPoESessions() {
  const qc = useQueryClient();
  const { confirm, ConfirmDialog } = useConfirm();
  const [disconnecting, setDisconnecting] = useState({});

  const { data: sessions = [], isLoading, isFetching } = useQuery({
    queryKey: ["pppoe-sessions"],
    queryFn: fetchAdminPPPoESessions,
    refetchInterval: 10 * 1000,
    staleTime: 10 * 1000,
  });

  const totalBytes = sessions.reduce((s, x) => s + x.rx_bytes + x.tx_bytes, 0);

  const handleDisconnect = async (username) => {
    const ok = await confirm({
      title: `Disconnect ${username}?`,
      description: "This will immediately terminate the PPPoE session for this user.",
      confirmText: "Disconnect",
      danger: true,
    });
    if (!ok) return;

    setDisconnecting((p) => ({ ...p, [username]: true }));
    try {
      await api.post("admin/pppoe/disconnect/", { username });
      toast.success(`${username} disconnected`);
      qc.invalidateQueries({ queryKey: ["pppoe-sessions"] });
    } catch {
      toast.error(`Failed to disconnect ${username}`);
    } finally {
      setDisconnecting((p) => ({ ...p, [username]: false }));
    }
  };

  return (
    <AdminLayout>
      <div className="space-y-6">
        <ConfirmDialog />

        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">PPPoE Sessions</h1>
            <p className="text-slate-400 text-sm mt-1">
              Real-time active connections — auto-refreshes every 10s
            </p>
          </div>
          <div className="flex items-center gap-2 text-sm text-slate-300">
            <span className={`w-2 h-2 rounded-full ${isFetching ? "bg-amber-400" : "bg-emerald-500/100 animate-pulse"}`} />
            {sessions.length} active
          </div>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { label: "Active Sessions", value: sessions.length },
            { label: "Total Bandwidth",  value: fmtMB(totalBytes) },
            { label: "Active Routers",   value: [...new Set(sessions.map((s) => s.router))].length },
            { label: "Auto-refresh",     value: "10s" },
          ].map((card) => (
            <div key={card.label} className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-4">
              <p className="text-xs text-slate-400 font-medium">{card.label}</p>
              <p className="text-2xl font-bold text-white mt-1">{card.value}</p>
            </div>
          ))}
        </div>

        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
          {isLoading ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-white/5 border-b border-white/10">
                  <tr>
                    {["Customer", "Username", "IP Address", "Uptime", "Download", "Upload", "Router", "Action"].map((h) => (
                      <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody><SkeletonTable rows={5} cols={8} /></tbody>
              </table>
            </div>
          ) : sessions.length === 0 ? (
            <div className="p-12 text-center">
              <p className="text-2xl mb-2">📡</p>
              <p className="text-slate-300 font-medium">No active PPPoE sessions</p>
              <p className="text-slate-500 text-sm mt-1">Waiting for connections…</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-white/5 border-b border-white/10">
                  <tr>
                    {["Customer", "Username", "IP Address", "Uptime", "Download", "Upload", "Router", "Action"].map((h) => (
                      <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {sessions.map((s, i) => (
                    <tr key={i} className="hover:bg-white/5 transition-colors">
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 bg-blue-100 text-blue-300 rounded-lg flex items-center justify-center font-bold text-xs flex-shrink-0">
                            {(s.customer || "?").charAt(0).toUpperCase()}
                          </div>
                          <span className="font-medium text-white">{s.customer || "Unknown"}</span>
                        </div>
                      </td>
                      <td className="px-5 py-3.5">
                        <code className="bg-white/5 text-slate-300 px-2 py-0.5 rounded text-xs">{s.username}</code>
                      </td>
                      <td className="px-5 py-3.5 text-slate-300 font-mono text-xs">{s.ip_address}</td>
                      <td className="px-5 py-3.5 text-slate-300 font-medium">{fmtUptime(s.uptime)}</td>
                      {/* Crossed over: rx and tx come straight off the router
                          and are counted from its side, so what it received is
                          what the subscriber uploaded. */}
                      <td className="px-5 py-3.5 text-blue-300 font-medium">{fmtMB(s.tx_bytes)}</td>
                      <td className="px-5 py-3.5 text-emerald-300 font-medium">{fmtMB(s.rx_bytes)}</td>
                      <td className="px-5 py-3.5">
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-white/5 text-slate-300">
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
