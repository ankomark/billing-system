import { useEffect, useState } from "react";
import api from "../../services/api";
import { useNavigate } from "react-router-dom";
import PPPoELiveStatus from "./PPPoEUsage";
import PPPoEControls from "./PPPoEControls";
import PPPoEUsageGraph from "../../components/usage/PPPoEUsageGraph";

export default function PPPoEPortal() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const loadData = async () => {
    try {
      const res = await api.get("pppoe/portal/");
      setData(res.data);
    } catch {
      setError("Failed to load PPPoE information");
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center text-red-600">
        {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        Loading PPPoE account...
      </div>
    );
  }

  if (data.status === "expired") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-100 p-6">
        <div className="bg-white p-6 rounded shadow max-w-md w-full text-center">
          <h2 className="text-xl font-bold text-red-600 mb-3">
            Subscription Expired
          </h2>
          <button
            onClick={() => navigate("/customer/pppoe/renew")}
            className="bg-blue-600 text-white px-4 py-2 rounded w-full"
          >
            Renew Subscription
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-100 p-6 flex justify-center">
      <div className="bg-white shadow rounded p-6 w-full max-w-lg">
        <h1 className="text-2xl font-bold text-center mb-4">
          PPPoE Account
        </h1>

        <div className="space-y-3 text-gray-800 text-sm">
          <p><b>Name:</b> {data.customer.full_name}</p>
          <p><b>Phone:</b> {data.customer.phone}</p>

          <div className="bg-gray-100 p-4 rounded">
            <p className="font-semibold">PPPoE Credentials</p>
            <p className="font-mono">{data.pppoe.username}</p>
            <p className="font-mono">{data.pppoe.password}</p>
          </div>

          <p><b>Package:</b> {data.package.name}</p>
          <p><b>Speed:</b> {data.package.download}M ↓ / {data.package.upload}M ↑</p>
          <p><b>Expires:</b> {new Date(data.expiry_date).toLocaleString()}</p>
        </div>

        <PPPoELiveStatus />
        <PPPoEUsageGraph />
        <PPPoEControls onAction={loadData} />

        <button
          onClick={() => navigate("/customer/pppoe/renew")}
          className="w-full mt-6 bg-blue-600 text-white py-2 rounded"
        >
          Renew Subscription
        </button>
      </div>
    </div>
  );
}
