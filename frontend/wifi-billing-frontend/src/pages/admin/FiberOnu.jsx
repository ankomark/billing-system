import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowLeft, MapPin } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Card, CardHeader, Chart, DataTable, Note, PageHeader, StatTile, StatusBadge } from "../../components/admin/ui";
import { fetchOnu, locateOnu, updateOnu } from "../../services/fiber";
import { Band, CAUSE_LABEL, DOWN_LABEL, OnuStatus, buttonCls, dbm, ghostCls, inputCls, metres, minutes, when } from "../../components/admin/fiber/shared";

const hourLabel = (iso) => new Date(iso).toLocaleString("en-KE", { day: "numeric", month: "short", hour: "2-digit" });

export default function FiberOnu() {
  const { id } = useParams();
  const qc = useQueryClient();
  const [days, setDays] = useState(14);
  const { data: onu, isLoading } = useQuery({ queryKey: ["fiber-onu", id, days], queryFn: () => fetchOnu(id, days) });
  const [label, setLabel] = useState(null);

  const saveLabel = useMutation({
    mutationFn: () => updateOnu(id, { label }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fiber-onu", id] }); setLabel(null); toast.success("Saved"); },
  });
  const locate = useMutation({
    mutationFn: (pos) => locateOnu(id, pos),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fiber-onu", id] }); toast.success("Location saved"); },
    onError: () => toast.error("Could not save the location"),
  });
  const here = () => {
    if (!navigator.geolocation) return toast.error("This device cannot give a location");
    navigator.geolocation.getCurrentPosition(
      (p) => locate.mutate({ latitude: p.coords.latitude, longitude: p.coords.longitude }),
      () => toast.error("Location permission was refused"),
      { enableHighAccuracy: true, timeout: 15000 },
    );
  };

  if (isLoading || !onu) {
    return <AdminLayout><div className="h-40 rounded-xl border border-white/10 bg-slate-900/60 animate-pulse" /></AdminLayout>;
  }
  const hasPower = onu.history.some((h) => h.rx_power_avg != null);

  return (
    <AdminLayout>
      <div className="space-y-6">
        <Link to="/admin/fiber?tab=onus" className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white">
          <ArrowLeft size={12} /> All ONUs
        </Link>
        <PageHeader title={onu.label} subtitle={`${onu.olt} ${onu.position} · ${onu.mac || "no MAC reported"}${onu.model ? ` · ${onu.model}` : ""}`}>
          <OnuStatus status={onu.status} />
          {onu.status === "online" && <Band band={onu.band} />}
        </PageHeader>

        {onu.degrading && (
          <Note tone="warning" title="Signal sliding">
            Received light is falling {Math.abs(onu.trend_db_per_day)} dB a day. It has not dropped yet: clean the
            connectors and check the drop cable for tight bends before it does.
          </Note>
        )}
        {onu.status === "offline" && (
          <Note tone="critical" title={`Offline since ${when(onu.last_down_at)}`}>
            Reason: {DOWN_LABEL[onu.last_down_reason] || "not reported"}.
            {onu.last_down_reason === "dying_gasp" ? " The ONU announced its own power failing — the customer's socket, not the fiber." : ""}
            {onu.last_down_reason === "los" ? " Light stopped arriving: a cut, a pulled connector or a broken drop cable." : ""}
          </Note>
        )}

        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <StatTile label="Received at ONU" value={dbm(onu.rx_power_dbm)} />
          <StatTile label="ONU transmits" value={dbm(onu.tx_power_dbm)} />
          <StatTile label="Arriving at OLT" value={dbm(onu.olt_rx_power_dbm)} />
          <StatTile label="Fiber distance" value={metres(onu.distance_m)} sub="along the cable" />
          <StatTile label="Temperature" value={onu.temperature_c != null ? `${onu.temperature_c} °C` : "—"} />
        </div>

        <Card>
          <CardHeader
            title="Received power"
            subtitle={hasPower ? `Hourly average, last ${days} days. Below −24 dBm is weak, below −27 critical.` : "No optical readings reported for this ONU yet"}
            action={<select className={`${inputCls} w-auto`} value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={2}>2 days</option><option value={14}>14 days</option><option value={90}>90 days</option>
            </select>}
          />
          <Chart kind="line" data={onu.history} xKey="hour" height={240}
                 series={[{ key: "rx_power_avg", label: "Received (dBm)" }, { key: "rx_power_min", label: "Lowest in hour" }]}
                 xTickFormatter={(v, i) => (i % Math.max(1, Math.floor(onu.history.length / 8)) === 0 ? hourLabel(v) : "")}
                 valueFormatter={(v) => (v == null ? "—" : `${v} dBm`)} labelFormatter={hourLabel}
                 empty="No history yet — it builds hour by hour from the first poll" />
        </Card>

        <Card>
          <CardHeader title="Traffic" subtitle="Peak rate in each hour" />
          <Chart kind="area" data={onu.history} xKey="hour" height={200}
                 series={[{ key: "rx_mbps", label: "Down (Mbps)" }, { key: "tx_mbps", label: "Up (Mbps)" }]}
                 xTickFormatter={(v, i) => (i % Math.max(1, Math.floor(onu.history.length / 8)) === 0 ? hourLabel(v) : "")}
                 valueFormatter={(v) => `${v} Mbps`} labelFormatter={hourLabel} empty="No traffic figures yet" />
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Customers behind it" subtitle="Found from the devices the OLT sees on this ONU" />
            <ul className="space-y-2 text-sm">
              {onu.customers.map((c) => (
                <li key={c.id} className="flex items-center justify-between">
                  <Link className="text-blue-300 hover:underline" to={`/admin/customers/${c.id}`}>{c.name}</Link>
                  <span className="text-xs text-slate-400">{c.phone} · <StatusBadge status={c.status} /></span>
                </li>
              ))}
              {!onu.customers.length && <li className="text-slate-500">No customer seen behind this ONU yet</li>}
            </ul>
          </Card>
          <Card>
            <CardHeader title="Location" subtitle={onu.located ? `Saved ${when(onu.located_at)}${onu.located_by ? ` by ${onu.located_by}` : ""}` : "Not on the map yet"} />
            {onu.located && (
              <p className="mb-3 text-sm text-slate-300">
                {onu.latitude.toFixed(5)}, {onu.longitude.toFixed(5)}
                {onu.location_note ? ` · ${onu.location_note}` : ""}{" "}
                <a className="text-blue-300 hover:underline" target="_blank" rel="noreferrer"
                   href={`https://www.google.com/maps?q=${onu.latitude},${onu.longitude}`}>Open in Maps</a>
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button className={buttonCls} onClick={here} disabled={locate.isPending}><MapPin size={14} /> Save where I am now</button>
              {label === null
                ? <button className={ghostCls} onClick={() => setLabel(onu.label_raw || "")}>Rename</button>
                : <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); saveLabel.mutate(); }}>
                    <input className={inputCls} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Mama Njeri, near church" />
                    <button className={buttonCls}>Save</button>
                  </form>}
            </div>
          </Card>
        </div>

        <Card padded={false}>
          <div className="px-5 pt-5"><CardHeader title="Outages" subtitle="The last ten incidents this ONU was part of" /></div>
          <DataTable
            columns={[
              { key: "c", label: "Cause", render: (r) => CAUSE_LABEL[r.cause] },
              { key: "s", label: "Scope", render: (r) => r.scope },
              { key: "w", label: "Started", render: (r) => when(r.started_at) },
              { key: "l", label: "Lasted", align: "right", render: (r) => minutes(r.minutes) },
            ]}
            rows={onu.outages} empty="Never been part of an outage"
          />
        </Card>
      </div>
    </AdminLayout>
  );
}
