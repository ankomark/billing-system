import api from "../../services/api";
import { useState } from "react";

export default function PPPoEControls({ onAction }) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const reconnect = async () => {
    setLoading(true);
    setMessage("");

    try {
      await api.post("/pppoe/reconnect/");
      setMessage("Reconnecting internet…");

      // Refresh portal data
      if (onAction) onAction();
    } catch (err) {
      console.error(err);
      setMessage("Reconnect failed. Please try again.");
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
