import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../../services/api";

export default function PppoeStatus() {
  const [params] = useSearchParams();
  const customerId = params.get("customer");

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadStatus = async () => {
    try {
      const res = await api.get(`/customers/${customerId}/pppoe-status/`);
      setData(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // Auto-refresh every 30 seconds
  useEffect(() => {
    loadStatus();

    const interval = setInterval(() => {
      loadStatus();
    }, 30000);

    return () => clearInterval(interval);
  }, [customerId]);

  if (!customerId) {
    return (
      <div className="p-6 text-center text-red-600">
        Missing customer ID
      </div>
    );
  }

  if (loading || !data) {
    return <div className="p-6 text-center">Loading...</div>;
  }

  const { username, password, package_name, expires_at, status } = data;

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center p-4">
      <div className="bg-white shadow p-6 rounded max-w-lg w-full">

        <h2 className="text-2xl font-bold mb-3 text-blue-700">
          PPPoE Account Status
        </h2>

        <div className="bg-gray-100 p-3 rounded mb-4 text-sm">
          <p><strong>Username:</strong> {username}</p>
          <p><strong>Password:</strong> {password}</p>
          <p><strong>Package:</strong> {package_name}</p>
          <p><strong>Expiry:</strong> {expires_at}</p>

          <p className={`font-semibold mt-2 ${status === "active" ? "text-green-600" : "text-red-600"}`}>
            Status: {status}
          </p>
        </div>

        <div className="text-left text-sm">
          <p className="mb-2 font-semibold">How to Connect:</p>
          <ul className="list-disc ml-6 text-gray-700">
            <li>Open your device's network settings.</li>
            <li>Select <strong>PPPoE connection</strong>.</li>
            <li>Enter the username and password provided above.</li>
            <li>Save & Connect.</li>
          </ul>
        </div>

        <p className="text-xs text-gray-500 mt-3">
          This page auto-refreshes every 30 seconds.
        </p>

      </div>
    </div>
  );
}
