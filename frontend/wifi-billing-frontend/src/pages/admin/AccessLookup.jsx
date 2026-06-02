import { useState, useCallback } from "react";
import toast from "react-hot-toast";
import AdminLayout from "../../components/admin/AdminLayout";
import { useConfirm } from "../../components/ui/ConfirmModal";
import StatusBadge from "../../components/ui/StatusBadge";
import { accessLookup, revokeAccess } from "../../services/api";

function fmt(dateString) {
  if (!dateString) return "—";
  return new Date(dateString).toLocaleString("en-KE", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function Row({ label, value, large }) {
  return (
    <div>
      <p className="text-xs text-slate-500 mb-0.5">{label}</p>
      <p className={`font-medium text-slate-800 ${large ? "text-lg" : "text-sm"}`}>
        {value || "—"}
      </p>
    </div>
  );
}

export default function AccessLookup() {
  const [query, setQuery]           = useState("");
  const [result, setResult]         = useState(null);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState("");
  const [reason, setReason]         = useState("");
  const [revoking, setRevoking]     = useState(false);
  const [activeTab, setActiveTab]   = useState("details");
  const { confirm, ConfirmDialog }  = useConfirm();

  const search = useCallback(async () => {
    if (!query.trim()) { setError("Enter a search term"); return; }
    setLoading(true); setError(""); setResult(null); setReason(""); setActiveTab("details");
    try {
      const { data } = await accessLookup(query.trim());
      setResult(data);
    } catch (err) {
      setError(err.response?.data?.detail || "No access record found for that query.");
    } finally {
      setLoading(false);
    }
  }, [query]);

  const handleKeyDown = (e) => { if (e.key === "Enter") search(); };

  const handleRevoke = async () => {
    if (!reason.trim()) {
      toast.error("A reason is required before revoking access.");
      return;
    }

    const what = result.voucher ? "voucher" : "subscription";
    const ok = await confirm({
      title: `Revoke ${what} access?`,
      description: `This will immediately disable internet for ${result.customer.name}. The customer's subscription will be marked as expired. This action cannot be undone.`,
      confirmText: "Revoke access",
      danger: true,
    });
    if (!ok) return;

    setRevoking(true);
    try {
      await revokeAccess(result.subscription.id, reason);
      toast.success("Access revoked successfully");
      setResult(null);
      setQuery("");
      setReason("");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to revoke access. Try again.");
    } finally {
      setRevoking(false);
    }
  };

  const tabs = [
    "details",
    "subscription",
    ...(result?.voucher ? ["voucher"] : []),
    "actions",
  ];

  return (
    <AdminLayout>
      <div className="space-y-6">
        <ConfirmDialog />

        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Access Lookup</h1>
          <p className="text-slate-500 text-sm mt-1">
            Search by voucher code, M-Pesa receipt, or customer phone number
          </p>
        </div>

        {/* Search */}
        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <div className="flex gap-3">
            <input
              type="text"
              className="flex-1 border border-slate-300 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              placeholder="WIFI-XXXXXX  ·  QK12345678  ·  2547XXXXXXXX"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
              autoFocus
            />
            <button
              onClick={search}
              disabled={loading || !query.trim()}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-semibold px-5 py-2.5 rounded-lg text-sm transition-colors flex-shrink-0"
            >
              {loading ? "Searching…" : "Search"}
            </button>
          </div>
        </div>

        {/* Error / not found */}
        {error && !loading && (
          <div className="bg-white rounded-xl border border-slate-200 p-8 text-center">
            <p className="text-slate-400 text-4xl mb-3">🔍</p>
            <p className="font-semibold text-slate-700">{error}</p>
            <button
              onClick={() => setError("")}
              className="mt-3 text-blue-600 text-sm hover:underline"
            >
              Clear and try again
            </button>
          </div>
        )}

        {/* Results */}
        {result && !loading && (
          <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
            {/* Tabs */}
            <div className="border-b border-slate-200 flex overflow-x-auto">
              {tabs.map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-5 py-3.5 text-sm font-medium capitalize whitespace-nowrap transition-colors ${
                    activeTab === tab
                      ? "text-blue-600 border-b-2 border-blue-600"
                      : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {tab === "details" ? "Customer" : tab}
                </button>
              ))}
            </div>

            <div className="p-6">
              {/* Customer tab */}
              {activeTab === "details" && (
                <div className="grid md:grid-cols-2 gap-6">
                  <div className="space-y-4">
                    <h3 className="font-semibold text-slate-800">Customer Information</h3>
                    <Row label="Full Name" value={result.customer.name} />
                    <Row label="Phone"     value={result.customer.phone} />
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Status</p>
                      <StatusBadge status={result.customer.status} />
                    </div>
                    <Row label="Connection" value={result.customer.connection_type} />
                  </div>
                  <div className="bg-slate-50 rounded-lg p-4 space-y-3">
                    <p className="font-medium text-slate-700 text-sm">Access Summary</p>
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-500">Lookup type</span>
                      <span className="font-medium capitalize">{result.type}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-500">Package</span>
                      <span className="font-medium">{result.subscription?.package || "—"}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-500">Sub status</span>
                      <StatusBadge status={result.subscription?.status} />
                    </div>
                  </div>
                </div>
              )}

              {/* Subscription tab */}
              {activeTab === "subscription" && (
                <div className="grid md:grid-cols-2 gap-6">
                  <div className="space-y-4">
                    <h3 className="font-semibold text-slate-800">Subscription Details</h3>
                    <Row label="Package"  value={result.subscription.package} large />
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Status</p>
                      <StatusBadge status={result.subscription.status} />
                    </div>
                    <Row label="Duration" value={result.subscription.duration || "—"} />
                  </div>
                  <div className="space-y-4">
                    <h3 className="font-semibold text-slate-800">Dates</h3>
                    <Row label="Expires" value={fmt(result.subscription.expires_at)} />
                    {result.subscription.expires_at &&
                      new Date(result.subscription.expires_at) > new Date() && (
                        <p className="text-xs text-emerald-600 font-semibold">
                          Active for{" "}
                          {Math.ceil(
                            (new Date(result.subscription.expires_at) - Date.now()) / 86400000
                          )}{" "}
                          more day(s)
                        </p>
                      )}
                  </div>
                </div>
              )}

              {/* Voucher tab */}
              {activeTab === "voucher" && result.voucher && (
                <div className="grid md:grid-cols-2 gap-6">
                  <div className="space-y-4">
                    <h3 className="font-semibold text-slate-800">Voucher Details</h3>
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Code</p>
                      <code className="bg-slate-100 text-slate-800 px-3 py-1.5 rounded-lg text-sm font-mono block">
                        {result.voucher.code}
                      </code>
                    </div>
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Status</p>
                      <StatusBadge status={result.voucher.is_active ? "active" : "expired"} />
                    </div>
                  </div>
                  <div className="space-y-4">
                    <Row label="Expires" value={fmt(result.voucher.expires_at)} />
                    <Row label="Created" value={fmt(result.voucher.created_at)} />
                  </div>
                </div>
              )}

              {/* Actions tab */}
              {activeTab === "actions" && (
                <div className="max-w-xl">
                  <div className="bg-red-50 border border-red-200 rounded-xl p-5">
                    <h3 className="font-semibold text-red-700 mb-1">Revoke Access</h3>
                    <p className="text-red-600 text-sm mb-4">
                      This immediately removes internet access for{" "}
                      <strong>{result.customer.name}</strong> and expires their subscription.
                      This action is permanent.
                    </p>
                    <textarea
                      className="w-full border border-red-200 rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-red-400 bg-white resize-none"
                      placeholder="Required: reason for revoking access…"
                      rows={3}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      disabled={revoking}
                    />
                    <div className="flex gap-3 mt-4">
                      <button
                        onClick={handleRevoke}
                        disabled={!reason.trim() || revoking}
                        className="bg-red-600 hover:bg-red-700 disabled:bg-red-300 text-white font-semibold px-5 py-2.5 rounded-lg text-sm transition-colors"
                      >
                        {revoking ? "Revoking…" : "Revoke Access"}
                      </button>
                      <button
                        onClick={() => setActiveTab("details")}
                        className="px-5 py-2.5 border border-slate-300 rounded-lg text-sm text-slate-700 hover:bg-slate-50 transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
