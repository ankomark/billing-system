import { useState } from "react";
import { reconnectPppoe } from "../../services/customerPortal";

export default function PPPoEControls({ onAction }) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const reconnect = async () => {
    setLoading(true);
    setMessage("");

    try {
      await reconnectPppoe();
      setMessage("Reconnecting… this takes a few seconds.");

      // The work is queued, so the portal has nothing new to say for a moment.
      // Refreshing immediately showed the old state and looked like nothing
      // happened.
      if (onAction) setTimeout(onAction, 4000);
    } catch (err) {
      setMessage(
        err.response?.data?.detail || "Reconnect failed. Please try again."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-6">
      {message && (
        <p className="text-sm text-center mb-3 text-blue-600">
          {message}
        </p>
      )}

      <button
        disabled={loading}
        onClick={reconnect}
        className={`w-full py-2 rounded font-semibold text-white ${
          loading
            ? "bg-gray-400 cursor-not-allowed"
            : "bg-green-600 hover:bg-green-700"
        }`}
      >
        {loading ? "Reconnecting..." : "Reconnect Internet"}
      </button>

      <p className="text-xs text-gray-500 mt-3 text-center">
        Use this if your internet disconnects without renewing.
      </p>
    </div>
  );
}
