import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Search, Receipt, CheckCircle2, XCircle } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import Pagination from "../../components/ui/Pagination";
import EmptyState from "../../components/ui/EmptyState";
import StatusBadge from "../../components/ui/StatusBadge";
import useDebounce from "../../hooks/useDebounce";
import { fetchMpesaTransactions } from "../../services/dashboard";

const PAGE_SIZE = 25;

/**
 * The M-Pesa ledger.
 *
 * Every one of these rows has been recorded since the callback was written —
 * receipt, amount, phone, the raw payload, whether it was applied and why not.
 * The only ones ever shown were the failures, so an operator holding a receipt
 * number a customer had read out over the phone had nowhere to type it.
 *
 * One list for both connection types, because it is one callback endpoint: the
 * connection type belongs to whoever the payment resolved to, not to the
 * transaction.
 */
export default function MpesaTransactions() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");

  const debounced = useDebounce(search, 400);

  const set = (fn) => (v) => { fn(v); setPage(1); };

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["mpesa-transactions", page, debounced, status, type],
    queryFn: () =>
      fetchMpesaTransactions({
        page, pageSize: PAGE_SIZE, search: debounced, status, connectionType: type,
      }),
    placeholderData: (prev) => prev,
  });

  const rows = data?.results ?? [];

  return (
    <AdminLayout>
      <div className="space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">
              M-Pesa transactions
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {data
                ? `${data.count.toLocaleString()} recorded — search by receipt, phone or customer`
                : "Every payment this account has received"}
            </p>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-white/5 disabled:opacity-50"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="relative flex-1">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              value={search}
              onChange={(e) => set(setSearch)(e.target.value)}
              placeholder="Receipt code, phone number, or customer name…"
              className="w-full rounded-lg border border-white/15 bg-slate-950 py-2 pl-9 pr-4 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <select
            value={status}
            onChange={(e) => set(setStatus)(e.target.value)}
            className="rounded-lg border border-white/15 bg-slate-900/80 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">All outcomes</option>
            <option value="success">Successful</option>
            <option value="failed">Failed</option>
          </select>
          <select
            value={type}
            onChange={(e) => set(setType)(e.target.value)}
            className="rounded-lg border border-white/15 bg-slate-900/80 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">PPPoE and hotspot</option>
            <option value="pppoe">PPPoE</option>
            <option value="hotspot">Hotspot</option>
          </select>
        </div>

        {isError && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-4 text-sm text-red-300">
            Couldn't load transactions. Try refreshing.
          </div>
        )}

        <div className="overflow-hidden rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-white/10 bg-white/5">
                <tr>
                  {["Receipt", "Customer", "Amount", "Phone", "Outcome", "When"].map((h) => (
                    <th
                      key={h}
                      className="whitespace-nowrap px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {isLoading ? (
                  <SkeletonTable rows={10} cols={6} />
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState
                        icon={<Receipt size={24} />}
                        title="Nothing here"
                        description={
                          debounced || status || type
                            ? "No transaction matches that. Check the receipt code — Safaricom's is ten characters."
                            : "No M-Pesa payment has been received yet."
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  rows.map((t) => (
                    <tr key={t.id} className="transition-colors hover:bg-white/5">
                      <td className="px-4 py-3">
                        <code className="font-mono text-xs text-slate-200">
                          {t.mpesa_receipt || "—"}
                        </code>
                        {t.invoice_number && (
                          <span className="mt-0.5 block text-[11px] text-slate-500">
                            {t.invoice_number}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {t.customer ? (
                          <button
                            onClick={() => navigate(`/admin/customers/${t.customer_id}`)}
                            className="text-left font-medium text-white hover:text-blue-400"
                          >
                            {t.customer}
                          </button>
                        ) : (
                          <span className="text-slate-500">unmatched</span>
                        )}
                        {t.connection_type && (
                          <span className="mt-0.5 block text-[11px] capitalize text-slate-500">
                            {t.connection_type}
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 font-semibold text-white">
                        KES {Number(t.amount || 0).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-300">
                        {t.phone_number || "—"}
                      </td>
                      <td className="px-4 py-3">
                        <Outcome tx={t} />
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-400">
                        {new Date(t.created_at).toLocaleString("en-KE", {
                          day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
                        })}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {data && (
          <Pagination
            page={page}
            totalPages={data.total_pages}
            count={data.count}
            pageSize={PAGE_SIZE}
            onPageChange={setPage}
          />
        )}
      </div>
    </AdminLayout>
  );
}

/**
 * Success and applied are different things, and the difference is the whole
 * reason to look. Money can arrive and still not reach a subscription — a
 * wrong amount, a reference that matched no invoice, a second receipt against
 * an invoice already paid.
 */
function Outcome({ tx }) {
  if (tx.status === "success" && tx.processed) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-400">
        <CheckCircle2 size={13} aria-hidden="true" /> Applied
      </span>
    );
  }
  if (tx.status === "success") {
    return (
      <span className="inline-flex flex-col gap-0.5">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-400">
          <XCircle size={13} aria-hidden="true" /> Paid, not applied
        </span>
        {tx.error_message && (
          <span className="text-[11px] text-slate-500">{tx.error_message}</span>
        )}
      </span>
    );
  }
  return (
    <span className="inline-flex flex-col gap-0.5">
      <StatusBadge status="failed" />
      {tx.error_message && (
        <span className="text-[11px] text-slate-500">{tx.error_message}</span>
      )}
    </span>
  );
}
