import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchCustomers } from "../../services/customers";
import { sendBroadcast } from "../../services/broadcast";

export default function Broadcast() {
  const [channel, setChannel] = useState("sms");
  const [audience, setAudience] = useState("all");
  const [message, setMessage] = useState("");
  const [customers, setCustomers] = useState([]);
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchCustomers().then(setCustomers).catch(() => {});
  }, []);

  const toggleCustomer = (id) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const handleSend = async () => {
    setError("");
    setResult(null);

    if (!message.trim()) {
      setError("Message is required");
      return;
    }

    const payload = {
      channel,
      audience,
      message,
      customer_ids: audience === "custom" ? selected : undefined,
    };

    if (audience === "custom" && selected.length === 0) {
      setError("Select at least one customer for custom broadcast");
      return;
    }

    setLoading(true);
    try {
      const data = await sendBroadcast(payload);
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
      <div className="p-6 space-y-6 max-w-4xl">
        <h1 className="text-2xl font-bold">Broadcast Messaging</h1>

        {error && (
          <div className="bg-red-100 text-red-700 p-3 rounded">{error}</div>
        )}

        {result && (
          <div className="bg-green-100 text-green-800 p-3 rounded">
            ✅ Sent: {result.sent} | ❌ Failed: {result.failed}
          </div>
        )}

        <div className="bg-white rounded shadow p-4 grid md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-semibold mb-1">Channel</label>
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              className="w-full border rounded px-3 py-2"
            >
              <option value="sms">SMS</option>
              <option value="whatsapp">WhatsApp</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-semibold mb-1">Audience</label>
            <select
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              className="w-full border rounded px-3 py-2"
            >
              <option value="all">All customers</option>
              <option value="active">Active only</option>
              <option value="expired">Expired only</option>
              <option value="custom">Custom selection</option>
            </select>
          </div>

          <div className="md:col-span-2">
            <label className="block text-sm font-semibold mb-1">Message</label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={5}
              className="w-full border rounded px-3 py-2"
              placeholder="Type your broadcast message..."
            />
          </div>

          {audience === "custom" && (
            <div className="md:col-span-2 border rounded p-3 max-h-64 overflow-auto">
              <p className="text-sm font-semibold mb-2">Select customers:</p>
              {customers.map((c) => (
                <label
                  key={c.id}
                  className="flex items-center gap-2 py-1 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(c.id)}
                    onChange={() => toggleCustomer(c.id)}
                  />
                  <span className="font-medium">{c.full_name}</span>
                  <span className="text-gray-500">({c.phone})</span>
                </label>
              ))}
            </div>
          )}

          <button
            disabled={loading}
            onClick={handleSend}
            className="md:col-span-2 bg-blue-600 hover:bg-blue-700 text-white py-2 rounded font-semibold disabled:opacity-50"
          >
            {loading ? "Sending..." : "Send Broadcast"}
          </button>
        </div>
      </div>
    </AdminLayout>
  );
}
