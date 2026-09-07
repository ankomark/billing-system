import { useQuery } from "@tanstack/react-query";
import { Radio, RefreshCw, WifiOff } from "lucide-react";
import { Card, CardHeader, Note, num } from "./ui";
import { fetchActiveClients } from "../../services/dashboard";

/**
 * Who is on the network right now, by site.
 *
 * The question an operator has constantly and could only answer by logging
 * into each MikroTik and reading /ip/hotspot/active by hand — per box, on a
 * phone, standing somewhere else entirely.
 *
 * The number is never presented as more current than it is. It comes from the
 * health sweep, which reads every router every two minutes, so it is a figure
 * with an age; the age is shown next to it rather than left for the reader to
 * assume. A router that has gone offline keeps its last count on screen,
 * labelled and struck out of the site total, because "12 clients" from a box
 * that stopped answering an hour ago is the one thing worse than no number.
 *
 * Stations are optional on this platform and most operators have none, so
 * routers without one are shown under "Unassigned" rather than being hidden or
 * given an invented site name.
 *
 * A site with one router shows one line. The per-router breakdown under a site
 * total is only information when there is more than one box to break down;
 * with one it is the same number printed twice, a line apart, which reads as a
 * bug in the arithmetic. This was how it shipped, and how it looked on the
 * estate it was built for — both sites there run a single router, which is the
 * normal shape rather than the exception. The router's name, its status dot
 * and the age of its count all survive onto the collapsed line; only the
 * repetition goes.
 */

function age(seconds) {
  if (seconds == null) return "never";
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

/**
 * A router's state, as one word.
 *
 * "offline" is reserved for a router the health sweep has condemned. A live
 * router whose count is merely late is "stale" and keeps its number: on
 * 2026-09-07 the worker pool filled, the health sweep stopped being scheduled,
 * and every count aged out — the panel struck both routers through and showed
 * a total of 0 while the hardware sat there serving. The operator was told
 * their network was down by a page describing a working network.
 */
const STATE = {
  offline: { dot: "bg-red-400",     label: "offline",   strike: true  },
  unknown: { dot: "bg-slate-500",   label: "no count",  strike: true  },
  stale:   { dot: "bg-amber-400",   label: null,        strike: false },
  fresh:   { dot: "bg-emerald-400", label: null,        strike: false },
};

function stateOf(router) {
  return STATE[router.state] || (router.fresh ? STATE.fresh : STATE.stale);
}

function RouterRow({ router }) {
  const st = stateOf(router);
  const unknown = router.active_clients == null;

  return (
    <div className="flex items-center justify-between gap-3 py-1.5 pl-4 text-sm">
      <span className="flex items-center gap-2 min-w-0">
        <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${st.dot}`}
              aria-hidden="true" />
        <span className="truncate text-slate-300">{router.name}</span>
      </span>

      <span className="flex items-center gap-2 flex-shrink-0">
        <span
          className={`tabular-nums ${
            st.strike ? "text-slate-500 line-through" : "text-slate-200"
          }`}
        >
          {unknown ? "—" : num(router.active_clients)}
        </span>
        <span className="text-[11px] text-slate-500 w-20 text-right">
          {st.label ?? age(router.age_seconds)}
        </span>
      </span>
    </div>
  );
}

export default function ActiveClientsPanel() {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["active-clients"],
    queryFn: fetchActiveClients,
    // The server's figure changes every two minutes at most, so asking more
    // often than that returns the same number and spends a request saying so.
    staleTime: 60 * 1000,
    refetchInterval: 2 * 60 * 1000,
  });

  const stations = data?.stations ?? [];

  return (
    <Card padded={false}>
      <CardHeader
        title="Active clients"
        subtitle="Live sessions on each site, hotspot and PPPoE together"
        chip="aqua"
        icon={Radio}
        action={
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs text-slate-300 hover:bg-white/5 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        }
      />

      <div className="px-5 py-4">
        {isError && (
          <Note tone="critical" title="Couldn't load active clients">
            <p>Check your connection and try again.</p>
          </Note>
        )}

        {isLoading && (
          <p className="text-sm text-slate-400">Reading the latest counts…</p>
        )}

        {!isLoading && !isError && stations.length === 0 && (
          <Note tone="info" title="No routers yet">
            <p>Add a router and its client count will appear here.</p>
          </Note>
        )}

        {!isLoading && !isError && stations.length > 0 && (
          <div className="space-y-4">
            {stations.map((s) => {
              // A breakdown of one is the same number twice, a line apart.
              // Most operators run a single box per site, so this is the
              // normal shape rather than the exception -- the per-router list
              // earns its place only when there is something to break down.
              const lone = s.routers.length === 1 ? s.routers[0] : null;

              return (
                <div key={s.station_id ?? "unassigned"}>
                  <div className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-1.5">
                    <h3 className="text-sm font-semibold text-slate-200 truncate">
                      {s.station_name ?? "Unassigned"}
                      {s.station_code && (
                        <span className="ml-2 text-[11px] font-normal text-slate-500">
                          {s.station_code}
                        </span>
                      )}
                      {/* Which box it is, kept even when its row is gone --
                          the site and the router rarely share a name, and the
                          operator still needs to know what to log into. */}
                      {lone && (
                        <span className="ml-2 text-[11px] font-normal text-slate-500">
                          · {lone.name}
                        </span>
                      )}
                    </h3>

                    <span className="flex items-baseline gap-1.5 flex-shrink-0">
                      {lone && (
                        <span
                          className={`mr-0.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
                            stateOf(lone).dot
                          }`}
                          aria-hidden="true"
                        />
                      )}
                      <span
                        className={`text-lg font-semibold tabular-nums ${
                          lone && stateOf(lone).strike
                            ? "text-slate-500 line-through"
                            : "text-emerald-300"
                        }`}
                      >
                        {/* The router's own figure when there is only one of
                            them. The station total excludes anything stale, so
                            a single stale box would read "0 active" beside a
                            struck-out number it disagreed with. */}
                        {lone
                          ? lone.active_clients == null
                            ? "—"
                            : num(lone.active_clients)
                          : num(s.active_clients)}
                      </span>
                      <span className="text-[11px] text-slate-500">
                        active
                        {lone
                          ? ` · ${stateOf(lone).label ?? age(lone.age_seconds)}`
                          : !s.complete && " · partial"}
                      </span>
                    </span>
                  </div>

                  {!lone && (
                    <div className="mt-1 divide-y divide-white/5">
                      {s.routers.map((r) => (
                        <RouterRow key={r.id} router={r} />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}

            {/* Only worth showing when there is more than one site to add up.
                A single-site operator already has the total above. */}
            {stations.length > 1 && (
              <div className="flex items-baseline justify-between border-t border-white/10 pt-3">
                <span className="text-sm text-slate-400">Everywhere</span>
                <span className="flex items-baseline gap-1.5">
                  <span className="text-lg font-semibold tabular-nums text-emerald-300">
                    {num(data.total_active_clients)}
                  </span>
                  <span className="text-[11px] text-slate-500">active</span>
                </span>
              </div>
            )}

            {/* Said once, at the bottom, rather than as a badge on every row
                it applies to. An operator needs to know the total is short;
                which box is missing is already visible above. */}
            {data && !data.complete && (
              <p className="flex items-start gap-1.5 text-[11px] text-amber-300/80">
                <WifiOff size={12} className="mt-0.5 flex-shrink-0" />
                <span>
                  A count here is older than usual, so these totals may lag.
                  Anything struck out is a router the health check cannot
                  reach; its figure is the last one it gave.
                </span>
              </p>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
