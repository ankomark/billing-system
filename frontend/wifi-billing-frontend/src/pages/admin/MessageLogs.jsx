import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  RefreshCw, Search, MessageSquare, CheckCircle2, XCircle, AlertTriangle,
} from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import Pagination from "../../components/ui/Pagination";
import EmptyState from "../../components/ui/EmptyState";
import useDebounce from "../../hooks/useDebounce";
import { fetchMessageLogs } from "../../services/dashboard";

const PAGE_SIZE = 25;

/**
 * What happened to the messages this account sent.
 *
 * M-Pesa has had a page like this since its callback was written. Messaging had
 * nothing: a failed send reached the server log and stopped there, which is a
 * file an operator cannot read and would not know to ask for. That is how a
 * rejected sender ID cost one of them a day — every message failing, a valid
 * API key, a credit balance that read correctly, and no way to see the one line
 * that said why.
 *
 * Opens on the failures, because somebody arriving here has messages that did
 * not arrive. The successes are kept too, and are one tab away: "we sent it and
 * the provider accepted it, here is when" is the answer to the support question
 * that prompted all of this.
 */
export default function MessageLogs() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("errors");
  const [channel, setChannel] = useState("");
  const [search, setSearch] = useState("");

  const debounced = useDebounce(search, 400);

  const set = (fn) => (v) => { fn(v); setPage(1); };

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["message-logs", page, status, channel, debounced],
    queryFn: () =>
      fetchMessageLogs({ page, pageSize: PAGE_SIZE, status, channel, search: debounced }),
    placeholderData: (prev) => prev,
  });

  const rows = data?.results ?? [];

  return (
    <AdminLayout>
      <div className="space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">
              Message delivery
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {data
                ? `${data.count.toLocaleString()} ${status === "errors" ? "undelivered" : "recorded"} — SMS and WhatsApp, with the provider's own reason`
                : "Every message this account has tried to send"}
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
              placeholder="Phone number, or a reason like 'sender ID'…"
              className="w-full rounded-lg border border-white/15 bg-slate-950 py-2 pl-9 pr-4 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <select
            value={status}
            onChange={(e) => set(setStatus)(e.target.value)}
            className="rounded-lg border border-white/15 bg-slate-900/80 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="errors">Did not arrive</option>
            <option value="">Everything</option>
            <option value="sent">Delivered to provider</option>
            <option value="refused">Refused by provider</option>
            <option value="failed">Never reached the provider</option>
          </select>
          <select
            value={channel}
            onChange={(e) => set(setChannel)(e.target.value)}
            className="rounded-lg border border-white/15 bg-slate-900/80 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">SMS and WhatsApp</option>
            <option value="sms">SMS</option>
            <option value="whatsapp">WhatsApp</option>
          </select>
        </div>

        {isError && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-4 text-sm text-red-300">
            Couldn't load the delivery log. Try refreshing.
          </div>
        )}

        <div className="overflow-hidden rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-white/10 bg-white/5">
                <tr>
                  {["Channel", "To", "Outcome", "Message", "When"].map((h) => (
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
                  <SkeletonTable rows={10} cols={5} />
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState
                        icon={<MessageSquare size={24} />}
                        title={status === "errors" ? "Everything got through" : "Nothing here"}
                        description={
                          debounced || channel
                            ? "No message matches that."
                            : status === "errors"
                            ? "No message has failed to send. This is the page you want to be empty."
                            : "No message has been sent yet."
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  rows.map((m) => (
                    <tr key={m.id} className="transition-colors hover:bg-white/5">
                      <td className="whitespace-nowrap px-4 py-3 text-slate-300">
                        {m.channel === "whatsapp" ? "WhatsApp" : "SMS"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium text-white">
                        {m.phone || "—"}
                      </td>
                      <td className="px-4 py-3">
                        <Outcome row={m} />
                      </td>
                      <td className="max-w-md px-4 py-3">
                        <span className="line-clamp-2 text-xs text-slate-400" title={m.message}>
                          {m.message || "—"}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-400">
                        {new Date(m.created_at).toLocaleString("en-KE", {
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
 * Refused and failed are different problems and want different actions.
 *
 * The provider refusing is something to go and fix — a sender ID, an empty
 * account, a number that will never route. Never reaching the provider is a
 * timeout or an outage, which may already have retried and succeeded, and
 * chasing it is usually wasted effort.
 */
function Outcome({ row }) {
  if (row.status === "sent") {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-400">
        <CheckCircle2 size={13} aria-hidden="true" /> Sent
      </span>
    );
  }

  const refused = row.status === "refused";

  return (
    <span className="inline-flex flex-col gap-0.5">
      <span
        className={`inline-flex items-center gap-1.5 text-xs font-medium ${
          refused ? "text-red-400" : "text-amber-400"
        }`}
      >
        {refused ? <XCircle size={13} aria-hidden="true" /> : <AlertTriangle size={13} aria-hidden="true" />}
        {refused ? "Refused" : "Not delivered"}
      </span>
      {row.reason && (
        <span className="max-w-xs text-[11px] leading-snug text-slate-500">
          {row.reason}
        </span>
      )}
    </span>
  );
}
