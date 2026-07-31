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
            <h1 className="text-2xl font-bold text-white tracking-tight">Failed M-Pesa Transactions</h1>
            <p className="text-slate-400 text-sm mt-1">
              STK push attempts that did not complete successfully
            </p>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-white/15 rounded-lg text-xs font-medium text-slate-300 hover:bg-white/5 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {isError && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-300 text-sm">
            Failed to load transactions. Try refreshing.
          </div>
        )}

        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-white/5 border-b border-white/10">
                <tr>
                  {["Receipt", "Phone", "Amount", "Error", "Time"].map((h) => (
                    <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {isLoading ? (
                  <SkeletonTable rows={6} cols={5} />
                ) : txs.length === 0 ? (
                  <tr>
                    <td colSpan="5" className="px-6 py-10 text-center text-slate-500 text-sm">
                      No failed transactions
                    </td>
                  </tr>
                ) : (
                  txs.map((tx) => (
                    <tr key={tx.id} className="hover:bg-white/5 transition-colors">
                      <td className="px-6 py-4">
                        <code className="bg-white/5 text-slate-300 px-2 py-0.5 rounded text-xs">
                          {tx.mpesa_receipt || "—"}
                        </code>
                      </td>
                      <td className="px-6 py-4 text-slate-300">{tx.phone_number}</td>
                      <td className="px-6 py-4 font-semibold text-white">KES {tx.amount}</td>
                      <td className="px-6 py-4">
                        <span className="text-red-300 text-xs">{tx.error_message || "Unknown error"}</span>
                      </td>
                      <td className="px-6 py-4 text-slate-500 text-xs whitespace-nowrap">
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
