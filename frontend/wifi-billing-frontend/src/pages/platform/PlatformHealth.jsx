import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  CreditCard,
  Router as RouterIcon,
} from "lucide-react";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { Card, PageHeader, StatTile } from "../../components/platform/ui";
import { fetchPlatformHealth } from "../../services/platform";

/**
 * What is wrong across the platform, in one place.
 *
 * The overview carried one failure number — routers offline — so anything else
 * needing attention had to be found by opening each operator in turn. Every row
 * here names the operator and links to them, because on this side of the
 * product a problem you cannot attribute is not yet actionable.
 */
export default function PlatformHealth() {
  const navigate = useNavigate();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["platform-health"],
    queryFn: fetchPlatformHealth,
    // Router state is refreshed by a sweep every two minutes; polling faster
    // than the data changes would only add load.
    refetchInterval: 60 * 1000,
    staleTime: 30 * 1000,
  });

  if (isError) {
    return (
      <PlatformLayout>
        <p className="text-slate-400">Couldn't load health. Try refreshing.</p>
      </PlatformLayout>
    );
  }

  return (
    <PlatformLayout>
      <div className="space-y-6 max-w-5xl">
        <PageHeader
          title="Health"
          subtitle="Everything across the platform that needs attention"
        />

        {isLoading ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-[92px] rounded-xl border border-white/10 bg-slate-900/60 animate-pulse"
              />
            ))}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <StatTile
                label="Routers offline"
                value={data.routers_offline.length}
                tone={data.routers_offline.length ? "critical" : "good"}
              />
              <StatTile
                label="Can't take payments"
                value={data.payments_unconfigured.length}
                tone={data.payments_unconfigured.length ? "warning" : "good"}
              />
              <StatTile
                label="Operators owing"
                value={data.operators_owing.length}
                tone={data.operators_owing.length ? "warning" : "good"}
              />
              <StatTile
                label="Failed payments"
                value={data.failed_payments_24h}
                sub="last 24 hours"
                tone={data.failed_payments_24h ? "serious" : "good"}
              />
            </div>

            {data.all_clear && (
              <Card>
                <div className="flex items-center gap-3 text-emerald-300">
                  <CheckCircle2 size={18} aria-hidden="true" />
                  <p className="text-sm">
                    Nothing needs attention. Every router is up, every operator
                    can take payments, and nobody is behind.
                  </p>
                </div>
              </Card>
            )}

            <Group
              title="Routers offline"
              icon={RouterIcon}
              tone="critical"
              rows={data.routers_offline}
              empty="Every active router is reachable."
              render={(r) => (
                <>
                  <span className="min-w-0">
                    <span className="block text-sm text-slate-100">
                      {r.router}{" "}
                      <span className="text-slate-500 font-normal">{r.ip_address}</span>
                    </span>
                    <span className="block text-xs text-slate-500">
                      {r.operator}
                      {r.last_error ? ` · ${r.last_error}` : ""}
                    </span>
                  </span>
                  <span className="text-xs text-slate-500 whitespace-nowrap">
                    {r.last_seen
                      ? `seen ${new Date(r.last_seen).toLocaleString("en-KE")}`
                      : "never seen"}
                  </span>
                </>
              )}
              onRow={(r) => navigate(`/platform/operators/${r.operator_id}`)}
            />

            <Group
              title="Can't take payments"
              icon={CreditCard}
              tone="warning"
              rows={data.payments_unconfigured}
              empty="Every operator has M-Pesa configured."
              hint="These operators have not finished M-Pesa setup, so no money can reach them. Nothing looks broken from their side — there is simply no revenue."
              render={(o) => (
                <span className="text-sm text-slate-100">{o.operator}</span>
              )}
              onRow={(o) => navigate(`/platform/operators/${o.operator_id}`)}
            />

            <Group
              title="Operators owing"
              icon={AlertTriangle}
              tone="warning"
              rows={data.operators_owing}
              empty="Nobody is behind on their platform invoices."
              render={(o) => (
                <>
                  <span className="text-sm text-slate-100">{o.operator}</span>
                  <span className="text-xs text-slate-400 capitalize">
                    {String(o.status).replace("_", " ")}
                  </span>
                </>
              )}
              onRow={(o) => navigate(`/platform/operators/${o.operator_id}`)}
            />
          </>
        )}
      </div>
    </PlatformLayout>
  );
}

const TONES = {
  critical: "text-red-300",
  warning: "text-amber-300",
  serious: "text-orange-300",
};

function Group({ title, icon: Icon, tone, rows, empty, hint, render, onRow }) {
  return (
    <Card padded={false}>
      <div className="px-5 py-4 border-b border-white/10">
        <div className="flex items-center gap-2">
          {/* Icon as well as colour: two of the status hues are close enough
              that colour alone would not separate them. */}
          <Icon size={15} className={TONES[tone]} aria-hidden="true" />
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          <span className="ml-auto text-xs text-slate-500">{rows.length}</span>
        </div>
        {hint && <p className="text-xs text-slate-500 mt-1.5">{hint}</p>}
      </div>

      {rows.length === 0 ? (
        <p className="px-5 py-6 text-sm text-slate-500">{empty}</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {rows.map((row, i) => (
            <li key={i}>
              <button
                onClick={() => onRow(row)}
                className="w-full flex items-center justify-between gap-4 px-5 py-3 text-left hover:bg-white/5 transition-colors"
              >
                {render(row)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
