import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { fetchPlatformInvoices, recordOperatorPayment } from "../../services/platform";

const KES = (v) => `KES ${Number(v || 0).toLocaleString()}`;

export default function PlatformInvoices() {
  const [params] = useSearchParams();
  const qc = useQueryClient();
  const [onlyOverdue, setOnlyOverdue] = useState(params.get("overdue") === "true");
  const [settling, setSettling] = useState(null);

  const { data, isLoading } = useQuery({
    queryKey: ["platform-invoices", onlyOverdue],
    queryFn: () => fetchPlatformInvoices({
      status: "unpaid",
      overdue: onlyOverdue,
    }),
    staleTime: 30 * 1000,
  });

  const invoices = data?.results ?? [];

  const settle = async (invoice) => {
    setSettling(invoice.id);
    try {
      await recordOperatorPayment({
        number: invoice.number,
        amount: invoice.amount,
        method: "mpesa",
        reference: window.prompt("M-Pesa reference (optional)") || "",
      });
      toast.success(`${invoice.operator} marked paid`);
      qc.invalidateQueries({ queryKey: ["platform-invoices"] });
      qc.invalidateQueries({ queryKey: ["platform-overview"] });
    } catch (e) {
      toast.error(e.response?.data?.detail || "Couldn't record the payment");
    } finally {
      setSettling(null);
    }
  };

  return (
    <PlatformLayout>
      <div className="space-y-5 max-w-5xl">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Invoices</h1>
            <p className="text-slate-500 text-sm mt-0.5">
              What operators owe the platform
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={onlyOverdue}
              onChange={(e) => setOnlyOverdue(e.target.checked)}
              className="rounded border-slate-300 text-teal-600"
            />
            Overdue only
          </label>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Invoice", "Operator", "Amount", "Due", ""].map((h) => (
                    <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading ? (
                  <SkeletonTable rows={5} cols={5} />
                ) : invoices.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-5 py-10 text-center text-slate-400 text-sm">
                      Nothing outstanding.
                    </td>
                  </tr>
                ) : (
                  invoices.map((inv) => (
                    <tr key={inv.id} className="hover:bg-slate-50">
                      <td className="px-5 py-3.5">
                        <code className="text-xs bg-slate-100 px-2 py-0.5 rounded">
                          {inv.number}
                        </code>
                      </td>
                      <td className="px-5 py-3.5 font-medium text-slate-800">
                        {inv.operator}
                      </td>
                      <td className="px-5 py-3.5 tabular-nums font-semibold text-slate-800">
                        {KES(inv.amount)}
                      </td>
                      <td className="px-5 py-3.5 text-xs whitespace-nowrap">
                        <span className={inv.is_overdue ? "text-red-600 font-semibold" : "text-slate-500"}>
                          {new Date(inv.due_date).toLocaleDateString("en-KE")}
                          {inv.is_overdue && " · overdue"}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <button
                          onClick={() => settle(inv)}
                          disabled={settling === inv.id}
                          className="bg-teal-600 hover:bg-teal-700 text-white px-3 py-1 rounded-md text-xs font-semibold disabled:opacity-50"
                        >
                          {settling === inv.id ? "…" : "Mark paid"}
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </PlatformLayout>
  );
}
