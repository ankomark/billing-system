import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchFailedMpesa } from "../../services/dashboard";

export default function FailedMpesa() {
  const [txs, setTxs] = useState([]);

  useEffect(() => {
    fetchFailedMpesa().then(setTxs);
  }, []);

  return (
    <AdminLayout>
      <div className="p-6">
        <h1 className="text-xl font-bold mb-6">
          Failed M-Pesa Transactions
        </h1>

        <table className="w-full bg-white shadow rounded">
          <thead className="bg-gray-200">
            <tr>
              <th className="p-3">Receipt</th>
              <th className="p-3">Phone</th>
              <th className="p-3">Amount</th>
              <th className="p-3">Error</th>
            </tr>
          </thead>

          <tbody>
            {txs.map((tx) => (
              <tr key={tx.id} className="border-t">
                <td className="p-3">{tx.mpesa_receipt || "-"}</td>
                <td className="p-3">{tx.phone_number}</td>
                <td className="p-3">KES {tx.amount}</td>
                <td className="p-3 text-red-600">
                  {tx.error_message || "Unknown error"}
                </td>
              </tr>
            ))}

            {txs.length === 0 && (
              <tr>
                <td colSpan="4" className="text-center p-4 text-gray-500">
                  No failed transactions
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </AdminLayout>
  );
}
