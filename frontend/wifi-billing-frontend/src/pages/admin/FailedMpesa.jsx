import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { fetchFailedMpesa } from "../../services/dashboard";

export default function FailedMpesa() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["failed-mpesa"],
    queryFn: fetchFailedMpesa,
    staleTime: 60 * 1000,
  });

  const txs = data?.results ?? [];

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Failed M-Pesa Transactions</h1>
            <p className="text-slate-500 text-sm mt-1">
              STK push attempts that did not complete successfully
            </p>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {isError && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-4 text-red-700 text-sm">
            Failed to load transactions. Try refreshing.
          </div>
        )}

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Receipt", "Phone", "Amount", "Error", "Time"].map((h) => (
                    <th key={h} className="px-6 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading ? (
                  <SkeletonTable rows={6} cols={5} />
                ) : txs.length === 0 ? (
                  <tr>
                    <td colSpan="5" className="px-6 py-10 text-center text-slate-400 text-sm">
                      No failed transactions
                    </td>
                  </tr>
                ) : (
                  txs.map((tx) => (
                    <tr key={tx.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4">
                        <code className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded text-xs">
                          {tx.mpesa_receipt || "—"}
                        </code>
                      </td>
                      <td className="px-6 py-4 text-slate-700">{tx.phone_number}</td>
                      <td className="px-6 py-4 font-semibold text-slate-800">KES {tx.amount}</td>
                      <td className="px-6 py-4">
                        <span className="text-red-600 text-xs">{tx.error_message || "Unknown error"}</span>
                      </td>
                      <td className="px-6 py-4 text-slate-400 text-xs whitespace-nowrap">
                        {new Date(tx.created_at).toLocaleString("en-KE")}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </AdminLayout>
  );
}
