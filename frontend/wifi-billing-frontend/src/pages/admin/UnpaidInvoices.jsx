import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchUnpaidInvoices } from "../../services/dashboard";

export default function UnpaidInvoices() {
  const [invoices, setInvoices] = useState([]);

  useEffect(() => {
    fetchUnpaidInvoices().then(setInvoices);
  }, []);

  return (
    <AdminLayout>
      <div className="p-6">
        <h1 className="text-xl font-bold mb-6">Unpaid Invoices</h1>

        <table className="w-full bg-white shadow rounded">
          <thead className="bg-gray-200 text-left">
            <tr>
              <th className="p-3">Invoice</th>
              <th className="p-3">Customer</th>
              <th className="p-3">Amount</th>
              <th className="p-3">Created</th>
            </tr>
          </thead>

          <tbody>
            {invoices.map((inv) => (
              <tr key={inv.id} className="border-t">
                <td className="p-3">{inv.invoice_number}</td>
                <td className="p-3">{inv.customer_name}</td>
                <td className="p-3">KES {inv.total_amount}</td>
                <td className="p-3">{inv.created_at}</td>
              </tr>
            ))}

            {invoices.length === 0 && (
              <tr>
                <td colSpan="4" className="text-center p-4 text-gray-500">
                  No unpaid invoices
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </AdminLayout>
  );
}
