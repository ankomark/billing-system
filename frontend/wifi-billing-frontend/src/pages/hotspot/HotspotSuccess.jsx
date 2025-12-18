import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

export default function HotspotSuccess() {
  const [params] = useSearchParams();

  const mac = params.get("mac") || "Unknown";
  const expires = params.get("expires") || "N/A";

  const [countdown, setCountdown] = useState(6);

  // Auto redirect after countdown
  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          // Redirect back to hotspot login page
          window.location.href = `http://login.hotspot/?username=${mac}`;
        }
        return c - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [mac]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-green-50 p-6">
      <div className="bg-white p-6 rounded shadow text-center max-w-md w-full">

        <h2 className="text-2xl font-bold text-green-600 mb-2">
          You're Connected!
        </h2>

        <p className="text-gray-700 mb-3">
          Your device has been granted internet access.
        </p>

        {/* Device Info */}
        <div className="bg-gray-100 p-3 rounded mb-4 text-sm">
          <p><strong>Device MAC:</strong> {mac}</p>
          <p><strong>Expires:</strong> {expires}</p>
        </div>

        {/* Manual reconnect button */}
        <button
          onClick={() => {
            window.location.href = `http://login.hotspot/?username=${mac}`;
          }}
          className="bg-blue-600 text-white w-full py-2 rounded font-semibold"
        >
          Reconnect Now
        </button>

        {/* Auto redirect countdown */}
        <p className="text-xs text-gray-600 mt-4">
          Redirecting automatically in <b>{countdown}</b> seconds...
        </p>

        <p className="text-xs text-gray-500 mt-1">
          If you disconnect later, simply reconnect to WiFi — no extra payment until expiry.
        </p>
      </div>
    </div>
  );
}
