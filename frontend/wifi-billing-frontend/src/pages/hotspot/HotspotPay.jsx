import { useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { purchaseHotspotPackage } from "../../services/hotspot";

export default function HotspotPay() {
  const [params] = useSearchParams();
  const navigate = useNavigate();

  // Passed through from HotspotPackages.jsx
  const packageId = params.get("package");
  const mac = params.get("mac");
  const tenantToken = params.get("t");

  const [phone, setPhone] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (!packageId || !mac) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-100 p-6">
        <div className="bg-white p-6 rounded-xl shadow text-center max-w-sm">
          <h2 className="text-xl font-bold text-red-600">Invalid request</h2>
          <p className="text-slate-600 mt-1">
            Please reconnect through the WiFi login page.
          </p>
        </div>
      </div>
    );
  }

  const handlePay = async () => {
    if (!phone.trim()) {
      setError("Enter the M-Pesa number to pay from.");
      return;
    }

    setError("");
    setLoading(true);
    try {
      // One public call creates the customer, subscription and invoice, then
      // sends the STK prompt. Previously this posted to the admin-only
      // subscriptions endpoint and always failed with 403.
      const { reference, poll_token: pollToken } = await purchaseHotspotPackage({
        tenantToken,
        packageId,
        phone: phone.trim(),
      });

      // The token travels with the reference. It is what lets the next page
      // read the voucher back; an invoice number alone no longer does.
      navigate(
        `/hotspot/status?ref=${encodeURIComponent(reference)}` +
          (pollToken ? `&token=${encodeURIComponent(pollToken)}` : "") +
          `&mac=${encodeURIComponent(mac)}` +
          (tenantToken ? `&t=${encodeURIComponent(tenantToken)}` : "")
      );
    } catch (e) {
      setError(
        e.response?.data?.detail ||
          "Couldn't start the payment. Please check the number and try again."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100 p-4">
      <div className="bg-white p-6 rounded-xl shadow w-full max-w-sm">
        <h2 className="text-lg font-bold text-center text-slate-800">
          Pay with M-Pesa
        </h2>
        <p className="text-xs text-slate-400 text-center mt-1 mb-4 font-mono">{mac}</p>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg mb-3 text-sm">
            {error}
          </div>
        )}

        <label className="text-sm font-medium text-slate-700">M-Pesa number</label>
        <input
          type="tel"
          inputMode="numeric"
          placeholder="0712345678"
          className="w-full border border-slate-300 rounded-lg p-3 mb-4 mt-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handlePay()}
        />

        <button
          onClick={handlePay}
          disabled={loading}
          className="w-full bg-blue-600 hover:bg-blue-700 text-white py-3 rounded-lg font-semibold disabled:opacity-50 transition-colors"
        >
          {loading ? "Sending prompt…" : "Pay & Connect"}
        </button>

        <p className="text-xs text-slate-400 text-center mt-3">
          You'll get a prompt on your phone to approve the payment.
        </p>
      </div>
    </div>
  );
}
