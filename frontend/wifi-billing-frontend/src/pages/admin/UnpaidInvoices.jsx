import { useQuery } from "@tanstack/react-query";
import { RefreshCw, FileText } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import EmptyState from "../../components/ui/EmptyState";
import { fetchUnpaidInvoices } from "../../services/dashboard";

export default function UnpaidInvoices() {
  const { data: invoices = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["unpaid-invoices"],
    queryFn: fetchUnpaidInvoices,
    staleTime: 60 * 1000,
  });

  const totalOwed = invoices.reduce((sum, inv) => sum + Number(inv.total_amount || 0), 0);

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Unpaid Invoices</h1>
            {!isLoading && (
              <p className="text-slate-500 text-sm mt-1">
                {invoices.length > 0
                  ? `${invoices.length} invoice${invoices.length !== 1 ? "s" : ""} · KES ${totalOwed.toLocaleString()} outstanding`
                  : "All invoices are settled"}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!isLoading && invoices.length > 0 && (
              <span className="inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold bg-amber-50 text-amber-700 border border-amber-200">
                {invoices.length} pending
              </span>
            )}
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {isError && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-4 text-red-700 text-sm">
            Failed to load invoices. Try refreshing.
          </div>
        )}

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Invoice #", "Customer", "Amount (KES)", "Created"].map((h) => (
                    <th key={h} className="px-6 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading ? (
                  <SkeletonTable rows={8} cols={4} />
                ) : invoices.length === 0 ? (
                  <tr>
                    <td colSpan={4}>
                      <EmptyState
                        icon={<FileText size={22} />}
                        title="No unpaid invoices"
                        description="All customer invoices have been settled."
                      />
                    </td>
                  </tr>
                ) : (
                  invoices.map((inv) => (
                    <tr key={inv.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4">
                        <code className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded text-xs font-mono">
                          {inv.invoice_number}
                        </code>
                      </td>
                      <td className="px-6 py-4 font-medium text-slate-800">{inv.customer_name}</td>
                      <td className="px-6 py-4 font-semibold text-slate-800">
                        {Number(inv.total_amount).toLocaleString()}
                      </td>
                      <td className="px-6 py-4 text-slate-500 text-xs whitespace-nowrap">
                        {new Date(inv.created_at).toLocaleString("en-KE", {
                          day: "numeric", month: "short", year: "numeric",
                          hour: "2-digit", minute: "2-digit",
                        })}
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
