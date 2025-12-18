import { useEffect, useState } from "react";
import api from "../../services/api";

export default function PPPoEUsage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadUsage = async () => {
    try {
      const res = await api.get("/pppoe/usage/");
      setData(res.data);
    } catch (err) {
      console.error("Failed to fetch PPPoE usage", err);
      setData({ connected: false });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsage();
    const interval = setInterval(loadUsage, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <p className="text-center text-sm text-gray-500 mt-4">
        Checking connection status…
      </p>
    );
  }

  if (!data || !data.connected) {
    return (
      <div className="mt-6 p-4 rounded bg-red-100 text-red-700 text-sm text-center">
        ❌ PPPoE not connected
      </div>
    );
  }

  return (
    <div className="bg-gray-100 p-4 rounded mt-6 text-sm">
      <h3 className="font-bold mb-3">Live Connection Status</h3>

      <p>
        <b>Status:</b>{" "}
        <span className="text-green-600 font-semibold">Connected</span>
      </p>

      <p><b>IP Address:</b> {data.ip_address}</p>
      <p><b>Uptime:</b> {data.uptime}</p>
      <p><b>Router:</b> {data.router}</p>

      {data.caller_id && (
        <p><b>Device MAC:</b> {data.caller_id}</p>
      )}
    </div>
  );
}
