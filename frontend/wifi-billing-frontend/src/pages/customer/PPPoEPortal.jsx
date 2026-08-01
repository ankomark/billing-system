import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchPortal } from "../../services/customerPortal";
import PPPoELiveStatus from "./PPPoEUsage";
import PPPoEControls from "./PPPoEControls";
import PPPoEUsageGraph from "../../components/usage/PPPoEUsageGraph";

export default function PPPoEPortal() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const loadData = async () => {
    try {
      setData(await fetchPortal());
      setError("");
    } catch (e) {
      setError(
        e.response?.status === 404
          ? "This login is not linked to a PPPoE account."
          : "Couldn't load your account. Check your connection."
      );
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  // An error used to be terminal: a red line, no retry, nothing to press. On a
  // page that polls every thirty seconds anyway, one blip stranded the customer.
  if (error && !data) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-100 p-6">
        <div className="bg-white rounded-2xl shadow p-6 max-w-sm w-full text-center">
          <p className="text-red-600 text-sm mb-4">{error}</p>
          <button
            onClick={loadData}
            className="w-full rounded-xl bg-blue-600 py-2.5 text-sm font-semibold text-white"
          >
            Try again
          </button>
        </div>
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

          {/* Both halves, or an honest explanation. A blank line where the
              password should be reads as a rendering fault; it means the
              credentials were never generated, which is something support can
              actually fix. */}
          <div className="bg-gray-100 p-4 rounded">
            <p className="font-semibold">PPPoE Credentials</p>
            {data.pppoe.username && data.pppoe.password ? (
              <>
                <p className="font-mono">{data.pppoe.username}</p>
                <p className="font-mono">{data.pppoe.password}</p>
              </>
            ) : (
              <p className="text-xs text-slate-500 mt-1">
                Your router credentials have not been issued yet. Please contact
                support.
              </p>
            )}
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
