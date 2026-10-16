import { Link } from "react-router-dom";
import { Activity, AlertTriangle, Cable, TrendingDown, Wrench } from "lucide-react";
import { Card, CardHeader, DataTable, Note, StatTile, StatusBadge, num } from "../ui";
import { Band, dbm, metres, when } from "./shared";

/**
 * The plant at a glance: one card per OLT, a tile per PON port.
 *
 * A port tile leads with what needs action -- offline, critical, weak -- and
 * only then how many are fine, because a page of green numbers hides the two
 * red ones that matter.
 */
export default function Overview({ data, onPort }) {
  if (!data.olts.length) {
    return (
      <Note tone="info" title="No OLTs registered yet">
        Add your VSOL and HiOSO OLTs under the OLTs tab. They need a management address on the
        network and SNMP read-only turned on before billing can read them.
      </Note>
    );
  }
  const t = data.totals || {};
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatTile label="ONUs in the field" value={num(t.total || 0)} icon={Cable} />
        <StatTile label="Online now" value={num(t.online || 0)} tone="good" icon={Activity} />
        <StatTile label="Offline" value={num(t.offline || 0)} tone={t.offline ? "critical" : "neutral"} />
        <StatTile label="Weak or critical signal" value={num((t.warning || 0) + (t.critical || 0))}
                  tone={t.critical ? "critical" : t.warning ? "warning" : "neutral"} icon={AlertTriangle} />
        <StatTile label="Signal sliding" value={num(data.degrading || 0)} sub="flagged before they drop"
                  tone={data.degrading ? "warning" : "neutral"} icon={TrendingDown} />
      </div>

      {data.olts.map((olt) => (
        <Card key={olt.id}>
          <CardHeader
            title={olt.name}
            subtitle={`${olt.vendor}${olt.model ? ` ${olt.model}` : ""} · ${olt.mgmt_ip}${
              olt.router ? ` · on ${olt.router} ${olt.uplink || ""}` : ""} · polled ${when(olt.last_polled_at)}`}
            action={<StatusBadge status={olt.reachable ? "online" : "offline"}
                                 label={olt.reachable ? "Reachable" : "Not answering"} />}
          />
          {!olt.reachable && olt.last_error && (
            <p className="mb-3 text-xs text-red-300">Last error: {olt.last_error}</p>
          )}
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {olt.ports.map((p) => (
              <button
                key={p.pon_port}
                onClick={() => onPort(olt.id, p.pon_port)}
                className={`rounded-xl border p-4 text-left transition-colors hover:bg-white/5 ${
                  p.open_outages ? "border-red-500/50 bg-red-500/5" : "border-white/10"
                }`}
              >
                <div className="flex items-baseline justify-between">
                  <p className="text-sm font-semibold text-white">PON {p.pon_port}</p>
                  <p className="text-xs text-slate-400">{p.total} ONUs</p>
                </div>
                {p.open_outages > 0 && (
                  <p className="mt-1 text-xs font-semibold text-red-300">Outage in progress</p>
                )}
                <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
                  <Count label="offline" n={p.offline} tone="text-red-300" />
                  <Count label="critical" n={p.critical} tone="text-red-300" />
                  <Count label="weak" n={p.warning} tone="text-amber-300" />
                  <Count label="online" n={p.online} tone="text-emerald-300" />
                  <Count label="no power data" n={p.power_unknown} tone="text-slate-400" />
                  <Count label="unlinked" n={p.unlinked} tone="text-slate-400" />
                </dl>
                <p className="mt-3 text-xs text-slate-400">
                  {p.peak_mbps != null
                    ? `Peak ${p.peak_mbps} Mbps · ${p.utilisation_pct}% of port`
                    : "No traffic figures yet"}
                </p>
              </button>
            ))}
          </div>
        </Card>
      ))}

      <Card padded={false}>
        <div className="px-5 pt-5">
          <CardHeader title="Weakest signals" subtitle="Online ONUs below −24 dBm, worst first: clean or fix before they drop" />
        </div>
        <DataTable
          columns={[
            { key: "label", label: "ONU", render: (r) => <Link className="text-blue-300 hover:underline" to={`/admin/fiber/onus/${r.id}`}>{r.label}</Link> },
            { key: "where", label: "Where", render: (r) => `${r.olt} ${r.position}` },
            { key: "rx", label: "Received", align: "right", render: (r) => dbm(r.rx_power_dbm) },
            { key: "band", label: "Level", render: (r) => <Band band={r.band} /> },
            { key: "distance", label: "Distance", align: "right", render: (r) => metres(r.distance_m) },
          ]}
          rows={data.worst_signal}
          empty="Every online ONU has a good signal"
        />
      </Card>

      <p className="text-xs text-slate-500 flex items-center gap-1.5">
        <Wrench size={12} /> {data.open_tickets} open repair ticket(s) · {data.open_outages} outage(s) in progress
      </p>
    </div>
  );
}

function Count({ label, n, tone }) {
  return (
    <div>
      <dd className={`text-lg font-bold ${n ? tone : "text-slate-600"}`}>{n || 0}</dd>
      <dt className="text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
    </div>
  );
}
