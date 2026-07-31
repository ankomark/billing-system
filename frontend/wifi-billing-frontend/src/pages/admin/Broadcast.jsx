import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchCustomers } from "../../services/customers";
import { sendBroadcast } from "../../services/broadcast";

export default function Broadcast() {
  const [channel, setChannel]   = useState("sms");
  const [audience, setAudience] = useState("all");
  const [message, setMessage]   = useState("");
  const [selected, setSelected] = useState([]);
  const [loading, setLoading]   = useState(false);
  const [result, setResult]     = useState(null);
  const [error, setError]       = useState("");

  const { data: customersData } = useQuery({
    queryKey: ["customers-all"],
    queryFn: () => fetchCustomers({ pageSize: 200 }),
    staleTime: 5 * 60 * 1000,
    enabled: audience === "custom",
  });
  const customers = customersData?.results ?? [];

  const toggleCustomer = (id) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const handleSend = async () => {
    setError("");
    setResult(null);

    if (!message.trim()) { setError("Message is required"); return; }
    if (audience === "custom" && selected.length === 0) {
      setError("Select at least one customer for custom broadcast");
      return;
    }

    setLoading(true);
    try {
      const data = await sendBroadcast({
        channel,
        audience,
        message,
        customer_ids: audience === "custom" ? selected : undefined,
      });
      setResult(data);
      setMessage("");
      setSelected([]);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to send broadcast");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AdminLayout>
      <div className="space-y-6 max-w-3xl">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Broadcast Messaging</h1>
          <p className="text-slate-400 text-sm mt-1">
            Send SMS or WhatsApp messages to customers in bulk
          </p>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 text-red-200 px-4 py-3 rounded-xl text-sm">
            {error}
          </div>
        )}

        {result && (
          <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-800 px-4 py-3 rounded-xl text-sm font-medium">
            Sent: {result.sent} &nbsp;·&nbsp; Failed: {result.failed}
          </div>
        )}

        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-6 space-y-5">
          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Channel</label>
              <select
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                className="w-full border border-white/15 bg-slate-950 text-slate-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-slate-900/80"
              >
                <option value="sms">SMS</option>
                <option value="whatsapp">WhatsApp</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Audience</label>
              <select
                value={audience}
                onChange={(e) => { setAudience(e.target.value); setSelected([]); }}
                className="w-full border border-white/15 bg-slate-950 text-slate-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-slate-900/80"
              >
                <option value="all">All customers</option>
                <option value="active">Active only</option>
                <option value="expired">Expired only</option>
                <option value="custom">Custom selection</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Message</label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={5}
              className="w-full border border-white/15 bg-slate-950 text-slate-100 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              placeholder="Type your broadcast message…"
            />
            <p className="text-xs text-slate-500 mt-1">{message.length} characters</p>
          </div>

          {audience === "custom" && (
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                Select customers ({selected.length} selected)
              </label>
              <div className="border border-white/10 rounded-lg max-h-56 overflow-y-auto divide-y divide-white/5">
                {customers.length === 0 ? (
                  <p className="px-4 py-6 text-center text-slate-500 text-sm">Loading customers…</p>
                ) : (
                  customers.map((c) => (
                    <label key={c.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-white/5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selected.includes(c.id)}
                        onChange={() => toggleCustomer(c.id)}
                        className="rounded border-white/15 text-blue-600"
                      />
                      <span className="text-sm font-medium text-white">{c.full_name}</span>
                      <span className="text-xs text-slate-500">{c.phone}</span>
                    </label>
                  ))
                )}
              </div>
            </div>
          )}

          <button
            disabled={loading || !message.trim()}
            onClick={handleSend}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white py-2.5 rounded-lg font-semibold text-sm transition-colors"
          >
            {loading ? "Sending…" : "Send Broadcast"}
          </button>
        </div>
      </div>
    </AdminLayout>
  );
}
