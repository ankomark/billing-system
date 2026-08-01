import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  fetchRenewalPackages,
  renewSubscription,
} from "../../services/customerPortal";

export default function PPPoERenew() {
  const [packages, setPackages] = useState([]);
  const [loadingPackages, setLoadingPackages] = useState(true);
  const [phone, setPhone]       = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    // This called `packages/`, which is operator staff only. Every subscriber
    // got a 403, and with no catch the page silently showed an empty list and
    // a disabled button — the renew flow could not be started at all.
    fetchRenewalPackages()
      .then((list) => { if (!cancelled) setPackages(list); })
      .catch(() => {
        if (!cancelled) {
          setError("Couldn't load the packages. Please try again in a moment.");
        }
      })
      .finally(() => { if (!cancelled) setLoadingPackages(false); });
    return () => { cancelled = true; };
  }, []);

  const handleRenew = async () => {
    if (!selected) { setError("Please select a package"); return; }
    if (!phone.trim()) { setError("Please enter your M-Pesa phone number"); return; }
    setError(""); setLoading(true);
    try {
      const { invoice_number: reference } = await renewSubscription({
        packageId: selected.id,
        phone: phone.trim(),
      });
      // By invoice, and to a route that exists. This used to send people to
      // /customer/pppoe/status?subscription=…, which was never routed — so
      // the last thing a subscriber saw after paying was the 404 page.
      navigate(`/customer/pppoe/status?ref=${encodeURIComponent(reference)}`);
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
          {loadingPackages && (
            <>
              <div className="h-[74px] animate-pulse rounded-xl bg-slate-100" />
              <div className="h-[74px] animate-pulse rounded-xl bg-slate-100" />
            </>
          )}
          {!loadingPackages && packages.length === 0 && !error && (
            <p className="rounded-xl bg-slate-50 p-4 text-center text-sm text-slate-500">
              No packages are available right now. Please contact support.
            </p>
          )}
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
                    {pkg.download_speed}/{pkg.upload_speed} Mbps · {pkg.duration}
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
