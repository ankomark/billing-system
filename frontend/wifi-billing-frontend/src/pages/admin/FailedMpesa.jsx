import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchFailedMpesa } from "../../services/dashboard";

export default function FailedMpesa() {
  const [txs, setTxs]         = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchFailedMpesa()
      .then((data) => setTxs(data?.results ?? []))
      .finally(() => setLoading(false));
  }, []);

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Failed M-Pesa Transactions</h1>
          <p className="text-slate-500 text-sm mt-1">
            STK push attempts that did not complete successfully
          </p>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {["Receipt", "Phone", "Amount", "Error", "Time"].map((h) => (
                  <th key={h} className="px-6 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="5" className="px-6 py-10 text-center text-slate-400 text-sm">Loading…</td></tr>
              ) : txs.length === 0 ? (
                <tr><td colSpan="5" className="px-6 py-10 text-center text-slate-400 text-sm">No failed transactions</td></tr>
              ) : (
                txs.map((tx) => (
                  <tr key={tx.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-4">
                      <code className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded text-xs">{tx.mpesa_receipt || "—"}</code>
                    </td>
                    <td className="px-6 py-4 text-slate-700">{tx.phone_number}</td>
                    <td className="px-6 py-4 font-semibold text-slate-800">KES {tx.amount}</td>
                    <td className="px-6 py-4">
                      <span className="text-red-600 text-xs">{tx.error_message || "Unknown error"}</span>
                    </td>
                    <td className="px-6 py-4 text-slate-400 text-xs">{new Date(tx.created_at).toLocaleString()}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </AdminLayout>
  );
}
