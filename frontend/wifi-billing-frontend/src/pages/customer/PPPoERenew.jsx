import { useState, useEffect } from "react";
import api from "../../services/api";
import { useNavigate } from "react-router-dom";

export default function PPPoERenew() {
  const [packages, setPackages] = useState([]);
  const [phone, setPhone]       = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    // packages/ is paginated — use .results
    api.get("packages/").then((res) => setPackages(res.data.results || res.data));
  }, []);

  const handleRenew = async () => {
    if (!selected) { setError("Please select a package"); return; }
    if (!phone.trim()) { setError("Please enter your M-Pesa phone number"); return; }
    setError(""); setLoading(true);
    try {
      const res = await api.post("pppoe/renew/", { package_id: selected.id, phone });
      navigate(`/customer/pppoe/status?subscription=${res.data.subscription_id}`);
    } catch (err) {
      setError(err.response?.data?.detail || "Payment failed to start. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-lg w-full max-w-lg p-8">
        <h1 className="text-2xl font-bold text-slate-800 mb-1">Renew Subscription</h1>
        <p className="text-slate-500 text-sm mb-6">Select a package and pay via M-Pesa</p>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm mb-4">
            {error}
          </div>
        )}

        {/* Phone */}
        <div className="mb-5">
          <label className="block text-sm font-medium text-slate-700 mb-1.5">M-Pesa Phone Number</label>
          <input
            type="tel"
            placeholder="2547XXXXXXXX"
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
        </div>

        {/* Package selection */}
        <p className="text-sm font-medium text-slate-700 mb-2">Choose Package</p>
        <div className="space-y-2 mb-6">
          {packages.map((pkg) => (
            <button
              key={pkg.id}
              type="button"
              onClick={() => setSelected(pkg)}
              className={`w-full text-left border rounded-xl p-4 transition-all ${
                selected?.id === pkg.id
                  ? "border-blue-500 bg-blue-50 ring-1 ring-blue-500"
                  : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"
              }`}
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-semibold text-slate-800">{pkg.name}</p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {pkg.download_speed}/{pkg.upload_speed} Mbps · {pkg.duration_value} {pkg.duration_unit}
                  </p>
                </div>
                <p className="font-bold text-blue-600 text-lg">KES {pkg.price}</p>
              </div>
            </button>
          ))}
        </div>

        <button
          disabled={loading || !selected || !phone.trim()}
          onClick={handleRenew}
          className="w-full bg-blue-600 hover:bg-blue-700 text-white py-3 rounded-xl font-semibold text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? "Processing…" : "Pay & Renew"}
        </button>

        <button
          onClick={() => navigate("/customer/pppoe")}
          className="w-full mt-3 text-slate-500 hover:text-slate-700 text-sm py-2 transition-colors"
        >
          ← Back to portal
        </button>
      </div>
    </div>
  );
}
