import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, ArrowRight, Gift } from "lucide-react";
import useDebounce from "../../hooks/useDebounce";
import { fetchCustomers } from "../../services/customers";
import CompAccessModal from "./CompAccessModal";
import { getUser } from "../../services/auth";
import { isOperatorAdmin } from "../../constants/roles";
import { Card, CardHeader } from "./ui";

/**
 * Finding one person, from the page an operator already has open.
 *
 * Looking someone up was a trip to the Customers page and a second search
 * there. This is the question asked most often — somebody is on the phone,
 * find them — so it belongs where the day starts.
 *
 * Searches everything the list does: name, phone, PPPoE username, voucher
 * code, M-Pesa receipt and device MAC.
 */
export default function CustomerQuickSearch() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [term, setTerm] = useState("");
  // The last step of the same errand: somebody rings about a failure, you
  // find them here, and you can put it right without leaving the page.
  const [gifting, setGifting] = useState(null);
  const canComp = isOperatorAdmin(getUser()?.role);
  const query = useDebounce(term.trim(), 350);

  const { data, isFetching } = useQuery({
    queryKey: ["customer-quick-search", query],
    queryFn: () => fetchCustomers({ search: query, pageSize: 6 }),
    // Nothing typed, nothing asked. A dashboard should not fetch a customer
    // list every time it loads for a search nobody has started.
    enabled: query.length >= 2,
    staleTime: 30 * 1000,
  });

  const rows = query.length >= 2 ? data?.results ?? [] : [];
  const total = data?.count ?? 0;

  const identifier = (c) =>
    c.connection_type === "hotspot"
      ? c.voucher_code || c.hotspot_username || ""
      : c.pppoe_username || "";

  return (
    <Card>
      <CompAccessModal
        open={!!gifting}
        customer={gifting}
        onClose={() => setGifting(null)}
        onDone={() => qc.invalidateQueries({ queryKey: ["customer-quick-search"] })}
      />
      <CardHeader
        title="Find a customer"
        subtitle="Name, phone, username, voucher code, M-Pesa receipt or MAC"
      />

      <div className="relative">
        <Search
          size={15}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
        />
        <input
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && term.trim()) {
              navigate(`/admin/customers?search=${encodeURIComponent(term.trim())}`);
            }
          }}
          placeholder="Start typing…"
          aria-label="Find a customer"
          className="w-full rounded-lg border border-white/15 bg-slate-950 py-2.5 pl-9 pr-4 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {query.length >= 2 && (
        <div className="mt-3">
          {isFetching && rows.length === 0 ? (
            <p className="px-1 py-3 text-sm text-slate-500">Searching…</p>
          ) : rows.length === 0 ? (
            <p className="px-1 py-3 text-sm text-slate-500">
              Nobody matches “{query}”.
            </p>
          ) : (
            <>
              <ul className="divide-y divide-white/5">
                {rows.map((c) => (
                  <li key={c.id} className="flex items-center gap-1">
                    {/* Two controls, side by side rather than nested — a button
                        inside a button is not markup a browser can honour. */}
                    <button
                      onClick={() => navigate(`/admin/customers/${c.id}`)}
                      className="flex min-w-0 flex-1 items-center gap-3 rounded-lg px-1 py-2.5 text-left transition-colors hover:bg-white/5"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-white">
                          {c.full_name}
                        </span>
                        <span className="block truncate text-xs text-slate-500">
                          {c.phone}
                          {identifier(c) && ` · ${identifier(c)}`}
                        </span>
                      </span>
                      <span
                        className={`flex-shrink-0 text-xs font-medium ${
                          c.status === "active" ? "text-emerald-400" : "text-slate-500"
                        }`}
                      >
                        {c.status}
                      </span>
                      <ArrowRight size={14} className="flex-shrink-0 text-slate-600" />
                    </button>
                    {canComp && (
                      /*
                        Divided from the row it sits beside. The row already
                        ends with an arrow meaning "open this"; a second grey
                        glyph against it read as part of the same cluster
                        rather than as a different action on the same person.
                      */
                      <button
                        onClick={() => setGifting(c)}
                        title="Give free access"
                        aria-label={`Give ${c.full_name} free access`}
                        className="ml-1 flex-shrink-0 rounded-lg border-l border-white/10 py-2 pl-3 pr-2 text-slate-600 transition-colors hover:text-emerald-300"
                      >
                        <Gift size={15} />
                      </button>
                    )}
                  </li>
                ))}
              </ul>

              {total > rows.length && (
                <button
                  onClick={() =>
                    navigate(`/admin/customers?search=${encodeURIComponent(query)}`)
                  }
                  className="mt-2 w-full rounded-lg py-2 text-xs font-medium text-blue-400 transition-colors hover:bg-white/5"
                >
                  See all {total.toLocaleString()} matches
                </button>
              )}
            </>
          )}
        </div>
      )}
    </Card>
  );
}
