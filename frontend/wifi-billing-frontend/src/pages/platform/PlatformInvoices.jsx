import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { Card, DataTable, PageHeader, KES } from "../../components/platform/ui";
import { fetchPlatformInvoices, recordOperatorPayment } from "../../services/platform";

export default function PlatformInvoices() {
  const [params] = useSearchParams();
  const qc = useQueryClient();
  const [onlyOverdue, setOnlyOverdue] = useState(params.get("overdue") === "true");
  const [settling, setSettling] = useState(null);

  const { data, isLoading } = useQuery({
    queryKey: ["platform-invoices", onlyOverdue],
    queryFn: () => fetchPlatformInvoices({ status: "unpaid", overdue: onlyOverdue }),
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

  const columns = [
    {
      key: "number",
      label: "Invoice",
      render: (inv) => (
        <code className="text-xs bg-white/5 border border-white/10 px-2 py-0.5 rounded text-slate-300">
          {inv.number}
        </code>
      ),
    },
    {
      key: "operator",
      label: "Operator",
      className: "font-medium text-slate-100",
    },
    {
      key: "amount",
      label: "Amount",
      align: "right",
      className: "font-semibold text-slate-100",
      render: (inv) => KES(inv.amount),
    },
    {
      key: "due_date",
      label: "Due",
      render: (inv) => (
        <span
          className={
            inv.is_overdue ? "text-red-300 font-semibold text-xs" : "text-slate-400 text-xs"
          }
        >
          {new Date(inv.due_date).toLocaleDateString("en-KE")}
          {inv.is_overdue && " · overdue"}
        </span>
      ),
    },
    {
      key: "action",
      label: "",
      align: "right",
      render: (inv) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            settle(inv);
          }}
          disabled={settling === inv.id}
          className="bg-teal-500 hover:bg-teal-400 text-slate-950 px-3 py-1 rounded-md text-xs font-semibold disabled:opacity-50 transition-colors"
        >
          {settling === inv.id ? "…" : "Mark paid"}
        </button>
      ),
    },
  ];

  return (
    <PlatformLayout>
      <div className="space-y-6 max-w-5xl">
        <PageHeader title="Invoices" subtitle="What operators owe the platform">
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={onlyOverdue}
              onChange={(e) => setOnlyOverdue(e.target.checked)}
              className="rounded border-white/20 bg-slate-900 text-teal-500 focus:ring-teal-500"
            />
            Overdue only
          </label>
        </PageHeader>

        <Card padded={false} className="overflow-hidden">
          {isLoading ? (
            <div className="px-5 py-10 text-center text-sm text-slate-500">
              Loading invoices…
            </div>
          ) : (
            <DataTable columns={columns} rows={invoices} empty="Nothing outstanding." />
          )}
        </Card>
      </div>
    </PlatformLayout>
  );
}
