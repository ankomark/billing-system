import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, PlugZap } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import Pagination from "../../components/ui/Pagination";
import EmptyState from "../../components/ui/EmptyState";
import { fetchConnectionAttempts } from "../../services/dashboard";

const PAGE_SIZE = 25;

const TONE = {
  invalid: "text-amber-300",
  device_limit: "text-blue-300",
  blocked: "text-red-300",
  no_provider: "text-slate-400",
};

/**
 * The connections that did not happen.
 *
 * Only successes were ever recorded, so an operator heard about the one
 * customer who complained and nothing about the twenty who mistyped a code and
 * gave up. This is the rest of them, with enough to act on: what was typed,
 * from which device, and why it was refused.
 *
 * Kept a fortnight. It answers "is anybody struggling to get on today", which
 * is a question about now.
 */
export default function ConnectionAttempts() {
  const [page, setPage] = useState(1);
  const [outcome, setOutcome] = useState("");

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["connection-attempts", page, outcome],
    queryFn: () => fetchConnectionAttempts({ page, pageSize: PAGE_SIZE, outcome }),
    placeholderData: (prev) => prev,
    refetchInterval: 60 * 1000,
  });

  const rows = data?.results ?? [];

  return (
    <AdminLayout>
      <div className="space-y-5">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">
              Failed connections
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {data
                ? `${data.count.toLocaleString()} in the last fortnight`
                : "Who tried to get on and couldn't"}
            </p>
          </div>
          <div className="flex flex-shrink-0 gap-2">
            <select
              value={outcome}
              onChange={(e) => { setOutcome(e.target.value); setPage(1); }}
              className="rounded-lg border border-white/15 bg-slate-900/80 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Every reason</option>
              <option value="invalid">Code not recognised</option>
              <option value="device_limit">Too many devices</option>
              <option value="blocked">Device blocked</option>
            </select>
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-2 text-xs font-medium text-slate-300 transition-colors hover:bg-white/5 disabled:opacity-50"
            >
              <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {isError && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-4 text-sm text-red-300">
            Couldn't load these. Try refreshing.
          </div>
        )}

        <div className="overflow-hidden rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-white/10 bg-white/5">
                <tr>
                  {["What they typed", "Device", "Why it failed", "When"].map((h) => (
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
                  <SkeletonTable rows={8} cols={4} />
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={4}>
                      <EmptyState
                        icon={<PlugZap size={24} />}
                        title="Nobody has been turned away"
                        description={
                          outcome
                            ? "Nothing matches that reason in the last fortnight."
                            : "Every attempt to connect has worked. That is the good outcome."
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  rows.map((a) => (
                    <tr key={a.id} className="transition-colors hover:bg-white/5">
                      <td className="px-4 py-3">
                        <code className="font-mono text-xs text-slate-200">
                          {a.code_tried || "—"}
                        </code>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-400">
                        {a.mac_address || "—"}
                      </td>
                      <td className={`px-4 py-3 text-xs font-medium ${TONE[a.outcome] || "text-slate-400"}`}>
                        {a.outcome_label}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-400">
                        {new Date(a.created_at).toLocaleString("en-KE", {
                          day: "numeric", month: "short",
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

        {rows.length > 0 && (
          <p className="text-xs text-slate-500">
            A code one character off a real one is usually a typo, and the same
            device failing repeatedly is usually somebody who needs help rather
            than somebody trying it on.
          </p>
        )}

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
