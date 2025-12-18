import { useEffect, useState } from "react";

export default function HotspotStatus() {
  const [status, setStatus] = useState("checking");
  const [expiresAt, setExpiresAt] = useState(null);

  // Extract MAC + Subscription ID from URL
  const query = new URLSearchParams(window.location.search);
  const mac = query.get("mac");
  const subscriptionId = query.get("subscription");

  // Poll backend every 3 seconds
  useEffect(() => {
    if (!mac || !subscriptionId) {
      setStatus("invalid_params");
      return;
    }

    const interval = setInterval(async () => {
      try {
        const res = await fetch(
          `${process.env.REACT_APP_API_URL}/api/subscriptions/${subscriptionId}/`
        );
        const data = await res.json();

        // Check payment status
        if (data.invoice?.payment_status === "paid") {
          setExpiresAt(data.expiry_date);
          setStatus("active");

          clearInterval(interval);

          // Wait 2 seconds → then redirect to hotspot auto-login
          setTimeout(() => {
            window.location.href = `/hotspot/success?mac=${mac}&expires=${data.expiry_date}`;
          }, 2000);
        } else {
          setStatus("pending");
        }
      } catch (error) {
        setStatus("error");
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [mac, subscriptionId]);

  const renderContent = () => {
    switch (status) {
      case "invalid_params":
        return (
          <>
            <h2 className="text-xl font-bold mb-2 text-red-600">
              Invalid Request
            </h2>
            <p className="text-gray-600">Missing MAC or subscription ID.</p>
          </>
        );

      case "checking":
      case "pending":
        return (
          <>
            <h2 className="text-xl font-bold mb-2">Processing Payment...</h2>
            <p className="text-gray-600">
              Please approve the M-Pesa STK push on your phone.
            </p>
            <p className="mt-3 text-blue-600 font-semibold animate-pulse">
              Waiting for payment confirmation...
            </p>
          </>
        );

      case "active":
        return (
          <>
            <h2 className="text-xl font-bold mb-2 text-green-600">
              Payment Confirmed!
            </h2>
            <p className="text-gray-700">Connecting your device...</p>
            <p className="mt-3 font-semibold text-gray-800">
              Package Expires: {expiresAt}
            </p>
          </>
        );

      case "error":
        return (
          <>
            <h2 className="text-xl font-bold mb-2 text-red-600">Error</h2>
            <p className="text-gray-700">
              Cannot reach the server. Check your network.
            </p>
          </>
        );

      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100 p-6">
      <div className="bg-white p-6 rounded shadow text-center w-full max-w-md">
        {renderContent()}
      </div>
    </div>
  );
}
