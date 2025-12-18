import { useState, useEffect } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import api from "../../services/api";

export default function HotspotPay() {
  const [params] = useSearchParams();
  const navigate = useNavigate();

  // Values sent from HotspotPackages.jsx
  const packageId = params.get("package");
  const mac = params.get("mac");
  const ip = params.get("ip");
  const mikrotikUser = params.get("username");

  const [phone, setPhone] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // If customer opens page directly → invalid
  if (!packageId || !mac) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-100">
        <div className="bg-white p-6 rounded shadow text-center">
          <h2 className="text-xl font-bold text-red-600">Invalid Request</h2>
          <p className="text-gray-600">Missing required hotspot parameters.</p>
        </div>
      </div>
    );
  }

  const handlePay = async () => {
    if (!phone) {
      setError("Please enter phone number.");
      return;
    }

    setError("");
    setLoading(true);

    try {
      // STEP 1: Create subscription on backend
      const subRes = await api.post("subscriptions/", {
        package: packageId,
        connection_type: "hotspot",
        hotspot_username: mac, // Important: bind MAC
      });

      const subscriptionId = subRes.data.id;

      // STEP 2: Initiate STK Push
      await api.post("mpesa/stk-push/", {
        subscription_id: subscriptionId,
        phone_number: phone,
      });

      // STEP 3: Redirect to status page
      navigate(
        `/hotspot/status?subscription=${subscriptionId}&mac=${mac}`
      );
    } catch (e) {
      console.error(e);
      setError(e.response?.data?.detail || "Payment initiation failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100 p-4">
      <div className="bg-white p-6 rounded shadow w-full max-w-sm">
        
        <h2 className="text-lg font-bold mb-4 text-center">
          Complete Your Payment
        </h2>

        {/* Show Device Identity */}
        <p className="text-sm text-gray-600 text-center mb-2">
          Connecting Device: <span className="font-mono">{mac}</span>
        </p>

        {error && (
          <div className="bg-red-100 text-red-700 p-2 mb-3 rounded">
            {error}
          </div>
        )}

        <label className="text-sm font-medium">M-Pesa Phone Number</label>
        <input
          type="tel"
          placeholder="2547XXXXXXXX"
          className="w-full border p-3 rounded mb-4 mt-1"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />

        <button
          onClick={handlePay}
          disabled={loading}
          className="w-full bg-blue-600 text-white py-2 rounded font-semibold disabled:opacity-50"
        >
          {loading ? "Sending STK..." : "Pay & Connect"}
        </button>
      </div>
    </div>
  );
}
