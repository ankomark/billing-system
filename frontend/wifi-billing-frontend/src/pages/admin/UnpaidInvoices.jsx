import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchUnpaidInvoices } from "../../services/dashboard";

export default function UnpaidInvoices() {
  const [invoices, setInvoices] = useState([]);
  const [loading, setLoading]   = useState(true);

  useEffect(() => {
    fetchUnpaidInvoices()
      .then(setInvoices)
      .finally(() => setLoading(false));
  }, []);

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Unpaid Invoices</h1>
            <p className="text-slate-500 text-sm mt-1">
              {invoices.length > 0 ? `${invoices.length} invoice${invoices.length > 1 ? "s" : ""} awaiting payment` : "All invoices are settled"}
            </p>
          </div>
          {invoices.length > 0 && (
            <span className="inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold bg-amber-50 text-amber-700 border border-amber-200">
              {invoices.length} pending
            </span>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {["Invoice #", "Customer", "Amount", "Created"].map((h) => (
                  <th key={h} className="px-6 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="4" className="px-6 py-10 text-center text-slate-400 text-sm">Loading…</td></tr>
              ) : invoices.length === 0 ? (
                <tr><td colSpan="4" className="px-6 py-10 text-center text-slate-400 text-sm">No unpaid invoices</td></tr>
              ) : (
                invoices.map((inv) => (
                  <tr key={inv.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-4">
                      <code className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded text-xs">{inv.invoice_number}</code>
                    </td>
                    <td className="px-6 py-4 font-medium text-slate-800">{inv.customer_name}</td>
                    <td className="px-6 py-4 font-semibold text-slate-800">KES {inv.total_amount}</td>
                    <td className="px-6 py-4 text-slate-500 text-xs">{new Date(inv.created_at).toLocaleString()}</td>
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
