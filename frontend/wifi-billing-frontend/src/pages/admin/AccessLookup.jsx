// import { useState, useCallback } from "react";
// import {
//   accessLookup,
//   deactivateVoucher,
//   revokeSubscription,
// } from "../../services/api";

// export default function AccessLookup() {
//   const [query, setQuery] = useState("");
//   const [result, setResult] = useState(null);
//   const [loading, setLoading] = useState(false);
//   const [error, setError] = useState("");
//   const [reason, setReason] = useState("");

//   const loadCustomer = useCallback(async () => {
//     if (!query.trim()) return;

//     setLoading(true);
//     setError("");
//     setResult(null);

//     try {
//       const { data } = await accessLookup(query);
//       setResult(data);
//     } catch (err) {
//       setError(err.response?.data?.detail || "No access found");
//     } finally {
//       setLoading(false);
//     }
//   }, [query]);

//   const handleDeactivate = async () => {
//     if (!reason.trim()) {
//       alert("Reason required");
//       return;
//     }

//     if (!window.confirm("Deactivate this access?")) return;

//     try {
//       if (result.voucher) {
//         await deactivateVoucher(result.voucher.id);
//       } else {
//         await revokeSubscription(result.subscription.id);
//       }

//       alert("Access revoked successfully");
//       setResult(null);
//       setQuery("");
//       setReason("");
//     } catch (err) {
//       alert(err.response?.data?.detail || "Action failed");
//     }
//   };

//   return (
//     <div className="p-6 max-w-4xl">
//       <h1 className="text-2xl font-bold mb-4">Access Lookup</h1>

//       <div className="flex gap-2 mb-6">
//         <input
//           className="border px-3 py-2 w-full rounded"
//           placeholder="Voucher / Mpesa / Phone"
//           value={query}
//           onChange={(e) => setQuery(e.target.value)}
//         />
//         <button
//           onClick={loadCustomer}
//           className="bg-blue-600 text-white px-4 rounded"
//         >
//           Search
//         </button>
//       </div>

//       {loading && <p>Searching...</p>}
//       {error && <p className="text-red-600">{error}</p>}

//       {result && (
//         <div className="border rounded p-4 space-y-4 bg-white shadow">
//           <p><b>Customer:</b> {result.customer.name}</p>
//           <p><b>Phone:</b> {result.customer.phone}</p>
//           <p><b>Status:</b> {result.customer.status}</p>

//           <p><b>Package:</b> {result.subscription.package}</p>
//           <p><b>Expires:</b> {new Date(result.subscription.expires_at).toLocaleString()}</p>

//           <textarea
//             className="border w-full p-2"
//             placeholder="Reason for deactivation"
//             value={reason}
//             onChange={(e) => setReason(e.target.value)}
//           />

//           <button
//             onClick={handleDeactivate}
//             className="bg-red-600 text-white px-4 py-2 rounded"
//           >
//             Deactivate Access
//           </button>
//         </div>
//       )}
//     </div>
//   );
// }


import { useState, useCallback } from "react";
import {
  accessLookup,
  deactivateVoucher,
  revokeSubscription,
} from "../../services/api";

export default function AccessLookup() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [deactivating, setDeactivating] = useState(false);
  const [activeTab, setActiveTab] = useState("details");

  const loadCustomer = useCallback(async () => {
    if (!query.trim()) {
      setError("Please enter a search term");
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);
    setReason("");
    setActiveTab("details");

    try {
      const { data } = await accessLookup(query.trim());
      setResult(data);
    } catch (err) {
      setError(
        err.response?.data?.detail || 
        err.response?.data?.message || 
        "No access records found for the provided search term"
      );
    } finally {
      setLoading(false);
    }
  }, [query]);

  const handleKeyPress = (event) => {
    if (event.key === "Enter") {
      loadCustomer();
    }
  };

  const handleDeactivate = async () => {
    if (!reason.trim()) {
      alert("Please provide a reason for deactivation");
      return;
    }

    const actionType = result.voucher ? "voucher" : "subscription";
    const confirmationMessage = `Are you sure you want to deactivate this ${actionType}? This action is irreversible and will immediately revoke access.`;

    if (!window.confirm(confirmationMessage)) {
      return;
    }

    setDeactivating(true);

    try {
      if (result.voucher) {
        await deactivateVoucher(result.voucher.id, { reason: reason.trim() });
      } else {
        await revokeSubscription(result.subscription.id, { reason: reason.trim() });
      }

      alert("Access successfully revoked");
      setResult(null);
      setQuery("");
      setReason("");
      setDeactivating(false);
    } catch (err) {
      alert(
        err.response?.data?.detail || 
        err.response?.data?.message || 
        "Failed to revoke access. Please try again."
      );
      setDeactivating(false);
    }
  };

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const getStatusBadgeClass = (status) => {
    const statusMap = {
      active: "bg-green-100 text-green-800",
      inactive: "bg-gray-100 text-gray-800",
      expired: "bg-yellow-100 text-yellow-800",
      suspended: "bg-orange-100 text-orange-800",
      revoked: "bg-red-100 text-red-800",
    };
    return statusMap[status] || "bg-gray-100 text-gray-800";
  };

  return (
    <div className="min-h-screen bg-gray-50 p-4 md:p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900">Access Management</h1>
          <p className="text-gray-600 mt-2">
            Search and manage customer access using voucher codes, M-Pesa receipts, or phone numbers
          </p>
        </div>

        {/* Search Card */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mb-8">
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Search Criteria
            </label>
            <div className="flex flex-col md:flex-row gap-4">
              <div className="flex-grow">
                <input
                  type="text"
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition"
                  placeholder="Enter voucher code, M-Pesa transaction ID, or customer phone number"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyPress={handleKeyPress}
                  disabled={loading}
                />
              </div>
              <button
                onClick={loadCustomer}
                disabled={loading || !query.trim()}
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium px-6 py-3 rounded-lg transition duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 whitespace-nowrap"
              >
                {loading ? (
                  <span className="flex items-center justify-center">
                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    Searching...
                  </span>
                ) : "Search Access"}
              </button>
            </div>
            <p className="text-sm text-gray-500 mt-2">
              You can search using any of: voucher codes (e.g., "VOUCH-ABC123"), M-Pesa transaction IDs, or customer phone numbers
            </p>
          </div>
        </div>

        {/* Loading State */}
        {loading && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-blue-600"></div>
            <p className="mt-4 text-gray-600 font-medium">Searching for access records...</p>
          </div>
        )}

        {/* Error State */}
        {error && !loading && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
            <div className="flex items-center justify-center text-center p-8">
              <div className="text-gray-500">
                <svg className="w-16 h-16 mx-auto text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <h3 className="mt-4 text-lg font-medium text-gray-900">No Results Found</h3>
                <p className="mt-2 text-gray-600">{error}</p>
                <button
                  onClick={() => setError("")}
                  className="mt-4 text-blue-600 hover:text-blue-800 font-medium"
                >
                  Clear error and try again
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Results Section */}
        {result && !loading && (
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
            {/* Tab Navigation */}
            <div className="border-b border-gray-200">
              <nav className="flex">
                <button
                  onClick={() => setActiveTab("details")}
                  className={`px-6 py-4 font-medium text-sm transition ${activeTab === "details" ? "text-blue-600 border-b-2 border-blue-600" : "text-gray-500 hover:text-gray-700"}`}
                >
                  Customer Details
                </button>
                <button
                  onClick={() => setActiveTab("subscription")}
                  className={`px-6 py-4 font-medium text-sm transition ${activeTab === "subscription" ? "text-blue-600 border-b-2 border-blue-600" : "text-gray-500 hover:text-gray-700"}`}
                >
                  Subscription
                </button>
                {result.voucher && (
                  <button
                    onClick={() => setActiveTab("voucher")}
                    className={`px-6 py-4 font-medium text-sm transition ${activeTab === "voucher" ? "text-blue-600 border-b-2 border-blue-600" : "text-gray-500 hover:text-gray-700"}`}
                  >
                    Voucher Details
                  </button>
                )}
                <button
                  onClick={() => setActiveTab("actions")}
                  className={`px-6 py-4 font-medium text-sm transition ${activeTab === "actions" ? "text-blue-600 border-b-2 border-blue-600" : "text-gray-500 hover:text-gray-700"}`}
                >
                  Actions
                </button>
              </nav>
            </div>

            {/* Tab Content */}
            <div className="p-6">
              {/* Customer Details Tab */}
              {activeTab === "details" && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <h3 className="text-lg font-semibold text-gray-900 mb-4">Customer Information</h3>
                    <div className="space-y-4">
                      <div>
                        <p className="text-sm text-gray-500">Full Name</p>
                        <p className="font-medium text-gray-900 text-lg">{result.customer.name}</p>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Phone Number</p>
                        <p className="font-medium text-gray-900">{result.customer.phone}</p>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Status</p>
                        <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${getStatusBadgeClass(result.customer.status)}`}>
                          {result.customer.status}
                        </span>
                      </div>
                    </div>
                  </div>
                  
                  <div className="bg-gray-50 rounded-lg p-4">
                    <h4 className="font-medium text-gray-900 mb-2">Quick Stats</h4>
                    <div className="space-y-2">
                      <div className="flex justify-between">
                        <span className="text-gray-600">Access Type:</span>
                        <span className="font-medium">{result.voucher ? "Voucher-based" : "Subscription-based"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-600">Last Updated:</span>
                        <span className="font-medium">{formatDate(result.updated_at || new Date().toISOString())}</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Subscription Tab */}
              {activeTab === "subscription" && (
                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-6">Subscription Details</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="space-y-4">
                      <div>
                        <p className="text-sm text-gray-500">Package Name</p>
                        <p className="font-medium text-gray-900 text-xl">{result.subscription.package}</p>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Subscription Status</p>
                        <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${getStatusBadgeClass(result.subscription.status)}`}>
                          {result.subscription.status}
                        </span>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Created On</p>
                        <p className="font-medium text-gray-900">{formatDate(result.subscription.created_at)}</p>
                      </div>
                    </div>
                    <div className="space-y-4">
                      <div>
                        <p className="text-sm text-gray-500">Expiration Date</p>
                        <p className="font-medium text-gray-900">{formatDate(result.subscription.expires_at)}</p>
                        <p className="text-sm text-gray-500 mt-1">
                          {new Date(result.subscription.expires_at) > new Date() 
                            ? "Active for " + Math.ceil((new Date(result.subscription.expires_at) - new Date()) / (1000 * 60 * 60 * 24)) + " more days"
                            : "Expired"}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Duration</p>
                        <p className="font-medium text-gray-900">{result.subscription.duration || "N/A"}</p>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Voucher Tab */}
              {activeTab === "voucher" && result.voucher && (
                <div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-6">Voucher Details</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="space-y-4">
                      <div>
                        <p className="text-sm text-gray-500">Voucher Code</p>
                        <p className="font-medium text-gray-900 font-mono text-lg bg-gray-50 p-3 rounded">{result.voucher.code}</p>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Voucher Status</p>
                        <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${result.voucher.is_active ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                          {result.voucher.is_active ? "Active" : "Inactive"}
                        </span>
                      </div>
                    </div>
                    <div className="space-y-4">
                      <div>
                        <p className="text-sm text-gray-500">Expiration Date</p>
                        <p className="font-medium text-gray-900">{formatDate(result.voucher.expires_at)}</p>
                      </div>
                      <div>
                        <p className="text-sm text-gray-500">Created On</p>
                        <p className="font-medium text-gray-900">{formatDate(result.voucher.created_at)}</p>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Actions Tab */}
              {activeTab === "actions" && (
                <div>
                  <div className="bg-red-50 border border-red-200 rounded-lg p-6">
                    <div className="flex items-center mb-4">
                      <svg className="w-6 h-6 text-red-600 mr-3" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                      </svg>
                      <div>
                        <h3 className="text-lg font-semibold text-red-700">Revoke Access</h3>
                        <p className="text-red-600 text-sm">
                          This action will immediately revoke customer access and cannot be undone.
                        </p>
                      </div>
                    </div>

                    <div className="mt-6">
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        Deactivation Reason *
                      </label>
                      <textarea
                        className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-red-500 focus:border-red-500 outline-none transition"
                        placeholder="Provide a detailed reason for revoking access..."
                        rows="4"
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        disabled={deactivating}
                      />
                      <p className="text-sm text-gray-500 mt-2">
                        This reason will be logged and may be reviewed by administrators.
                      </p>
                    </div>

                    <div className="mt-6 flex gap-3">
                      <button
                        onClick={handleDeactivate}
                        disabled={!reason.trim() || deactivating}
                        className="bg-red-600 hover:bg-red-700 disabled:bg-red-300 text-white font-medium px-6 py-3 rounded-lg transition duration-200 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2"
                      >
                        {deactivating ? (
                          <span className="flex items-center justify-center">
                            <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                            Revoking Access...
                          </span>
                        ) : `Revoke ${result.voucher ? "Voucher" : "Subscription"}`}
                      </button>
                      <button
                        onClick={() => setActiveTab("details")}
                        className="px-6 py-3 border border-gray-300 rounded-lg font-medium text-gray-700 hover:bg-gray-50 transition"
                      >
                        Cancel
                      </button>
                    </div>

                    <div className="mt-6 p-4 bg-red-100 rounded-lg">
                      <p className="text-sm text-red-700 font-medium">⚠️ Important Notes:</p>
                      <ul className="text-sm text-red-600 mt-2 space-y-1">
                        <li>• This action is immediate and irreversible</li>
                        <li>• Customer will lose access immediately</li>
                        <li>• Deactivation reason is required for audit purposes</li>
                        {result.voucher && <li>• Voucher will be permanently deactivated</li>}
                        {!result.voucher && <li>• Subscription will be terminated</li>}
                      </ul>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}