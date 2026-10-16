import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Card, CardHeader, DataTable, Note, StatTile, StatusBadge, KES, num } from "../ui";
import {
  createMaintenance, createOlt, createSplitter, fetchSplitterSuggestions, fetchSplitters, deleteMaintenance, fetchMaintenance, fetchMonthlyReport, fetchOlts,
  fetchOnus, fetchOutage, fetchOutages, fetchPortReport, fetchTickets, pollOlt, updateOlt, updateTicket,
} from "../../../services/fiber";
import { Band, CAUSE_LABEL, OnuStatus, buttonCls, dbm, ghostCls, inputCls, metres, minutes, when } from "./shared";

const errText = (e) => {
  const d = e?.response?.data;
  if (!d) return "Something went wrong";
  if (typeof d === "string") return d;
  return d.detail || Object.entries(d).map(([k, v]) => `${k}: ${[].concat(v).join(" ")}`).join("; ");
};

/* ------------------------------------------------------------------ ONUs */

export function OnuPanel({ filter, setFilter }) {
  const [q, setQ] = useState(filter.q || "");
  const { data = [], isLoading } = useQuery({
    queryKey: ["fiber-onus", filter],
    queryFn: () => fetchOnus(filter),
    staleTime: 60 * 1000,
  });
  const set = (k, v) => setFilter({ ...filter, [k]: v || undefined });
  return (
    <Card padded={false}>
      <div className="flex flex-wrap items-end gap-2 px-5 pt-5 pb-3">
        <form onSubmit={(e) => { e.preventDefault(); set("q", q); }} className="flex-1 min-w-[220px]">
          <input className={inputCls} placeholder="Search label, MAC, customer name or phone"
                 value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <Select value={filter.status} onChange={(v) => set("status", v)}
                options={[["", "Any status"], ["online", "Online"], ["offline", "Offline"]]} />
        <Select value={filter.band} onChange={(v) => set("band", v)}
                options={[["", "Any signal"], ["critical", "Critical"], ["warning", "Weak"], ["good", "Good"]]} />
        <Select value={filter.port} onChange={(v) => set("port", v)}
                options={[["", "All ports"], ["1", "PON 1"], ["2", "PON 2"], ["3", "PON 3"], ["4", "PON 4"]]} />
        <Select value={filter.extra} onChange={(v) => setFilter({ ...filter, extra: v || undefined,
                  unlinked: v === "unlinked" ? 1 : undefined, unlocated: v === "unlocated" ? 1 : undefined,
                  degrading: v === "degrading" ? 1 : undefined })}
                options={[["", "Everything"], ["degrading", "Signal sliding"], ["unlinked", "No customer linked"],
                          ["unlocated", "Not on the map"]]} />
        <Select value={filter.sort} onChange={(v) => set("sort", v)}
                options={[["", "By position"], ["signal", "Worst signal first"], ["distance", "Farthest first"]]} />
      </div>
      <DataTable
        loading={isLoading}
        columns={[
          { key: "label", label: "ONU", render: (r) => (
            <Link className="text-blue-300 hover:underline" to={`/admin/fiber/onus/${r.id}`}>{r.label}</Link>) },
          { key: "pos", label: "Where", render: (r) => `${r.olt} ${r.position}` },
          { key: "status", label: "Status", render: (r) => <OnuStatus status={r.status} /> },
          { key: "rx", label: "Received", align: "right", render: (r) => dbm(r.rx_power_dbm) },
          { key: "band", label: "Signal", render: (r) => (r.status === "online" ? <Band band={r.band} /> : "—") },
          { key: "dist", label: "Distance", align: "right", render: (r) => metres(r.distance_m) },
          { key: "trend", label: "Trend", align: "right", hideBelow: "lg", render: (r) =>
            r.degrading ? <span className="text-amber-300">{r.trend_db_per_day} dB/day</span> : "steady" },
          { key: "linked", label: "Customer", hideBelow: "md", render: (r) => (r.linked ? "linked" : <span className="text-slate-500">none</span>) },
          { key: "map", label: "Map", hideBelow: "md", render: (r) => (r.located ? "placed" : <span className="text-slate-500">—</span>) },
        ]}
        rows={data}
        empty="No ONUs match"
      />
    </Card>
  );
}

function Select({ value, onChange, options }) {
  return (
    <select value={value || ""} onChange={(e) => onChange(e.target.value)}
            className="rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100">
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  );
}

/* ------------------------------------------------------------------ outages */

export function OutagePanel() {
  const [open, setOpen] = useState(null);
  const { data = [], isLoading } = useQuery({ queryKey: ["fiber-outages"], queryFn: () => fetchOutages(30),
                                              refetchInterval: 60 * 1000 });
  const detail = useQuery({ queryKey: ["fiber-outage", open], queryFn: () => fetchOutage(open), enabled: !!open });
  return (
    <div className="space-y-4">
      <Card padded={false}>
        <div className="px-5 pt-5">
          <CardHeader title="Outages" subtitle="ONUs that went down together, as one incident · last 30 days, open first" />
        </div>
        <DataTable
          loading={isLoading}
          onRowClick={(r) => setOpen(r.id)}
          columns={[
            { key: "state", label: "", render: (r) => <StatusBadge status={r.ended_at ? "resolved" : "open"} label={r.ended_at ? "Over" : "Ongoing"} /> },
            { key: "cause", label: "Cause", render: (r) => CAUSE_LABEL[r.cause] || r.cause_label },
            { key: "where", label: "Where", render: (r) => `${r.olt}${r.pon_port != null ? ` PON ${r.pon_port}` : ""}` },
            { key: "start", label: "Started", render: (r) => when(r.started_at) },
            { key: "len", label: "Lasted", align: "right", render: (r) => minutes(r.minutes) },
            { key: "onus", label: "ONUs", align: "right", render: (r) => r.onus_affected },
            { key: "cust", label: "Customers", align: "right", render: (r) => r.customers_affected },
            { key: "risk", label: "At risk / day", align: "right", hideBelow: "md", render: (r) => KES(r.revenue_at_risk_per_day) },
            { key: "zone", label: "Fault zone", hideBelow: "lg", render: (r) =>
              r.nearest_m != null ? `${metres(r.nearest_m)} – ${metres(r.farthest_m)}` : "—" },
            { key: "told", label: "Texted / compensated", hideBelow: "lg", render: (r) =>
              `${r.notified_count || 0} / ${r.compensated_count || 0}` },
          ]}
          rows={data}
          empty="No outages in the last 30 days"
        />
      </Card>
      {open && detail.data && (
        <Card>
          <CardHeader title={`${CAUSE_LABEL[detail.data.cause]} · ${detail.data.olt}${detail.data.pon_port != null ? ` PON ${detail.data.pon_port}` : ""}`}
                      subtitle={`${detail.data.onus_affected} ONUs · ${detail.data.customers_affected} customers`}
                      action={<button className={ghostCls} onClick={() => setOpen(null)}>Close</button>} />
          {detail.data.nearest_m != null && (
            <Note tone="info" title="Where to look">
              The dark ONUs sit {metres(detail.data.nearest_m)} to {metres(detail.data.farthest_m)} along the fiber.
              {detail.data.farthest_unaffected_m != null &&
                ` The farthest ONU still working on this port is at ${metres(detail.data.farthest_unaffected_m)}, so on a shared trunk the break is past that point.`}
            </Note>
          )}
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">ONUs</p>
              <ul className="space-y-1 text-sm">
                {detail.data.onus.map((o) => (
                  <li key={o.id}><Link className="text-blue-300 hover:underline" to={`/admin/fiber/onus/${o.id}`}>{o.label}</Link>
                    <span className="text-slate-500"> · {o.position} · {metres(o.distance_m)}</span></li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Customers affected</p>
              <ul className="space-y-1 text-sm">
                {detail.data.customers.map((c) => (
                  <li key={c.id}><Link className="text-blue-300 hover:underline" to={`/admin/customers/${c.id}`}>{c.name}</Link>
                    <span className="text-slate-500"> · {c.phone}</span></li>
                ))}
                {!detail.data.customers.length && <li className="text-slate-500">None linked</li>}
              </ul>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ tickets */

export function TicketPanel() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("open");
  const { data = [], isLoading } = useQuery({ queryKey: ["fiber-tickets", status], queryFn: () => fetchTickets(status) });
  const save = useMutation({
    mutationFn: ({ id, body }) => updateTicket(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fiber-tickets"] }); toast.success("Ticket updated"); },
    onError: (e) => toast.error(errText(e)),
  });
  return (
    <Card padded={false}>
      <div className="flex items-center justify-between px-5 pt-5">
        <CardHeader title="Repair tickets" subtitle="Raised by the system for cuts and sliding signals, or by hand" />
        <Select value={status} onChange={setStatus} options={[["open", "Open"], ["resolved", "Resolved"], ["", "All"]]} />
      </div>
      <DataTable
        loading={isLoading}
        columns={[
          { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
          { key: "title", label: "Ticket", render: (r) => (
            <div><p className="text-slate-100">{r.title}</p><p className="text-xs text-slate-400 mt-0.5 max-w-xl">{r.detail}</p></div>) },
          { key: "onu", label: "ONU", hideBelow: "md", render: (r) => r.onu ? <Link className="text-blue-300 hover:underline" to={`/admin/fiber/onus/${r.onu}`}>{r.onu_label}</Link> : "—" },
          { key: "when", label: "Raised", render: (r) => when(r.created_at) },
          { key: "who", label: "Assigned", hideBelow: "md", render: (r) => r.assigned_to || "—" },
          { key: "act", label: "", render: (r) => r.status !== "resolved" ? (
            <div className="flex gap-1">
              <button className={ghostCls} onClick={() => {
                const who = window.prompt("Assign to (technician name)", r.assigned_to || "");
                if (who != null) save.mutate({ id: r.id, body: { status: "assigned", assigned_to: who } });
              }}>Assign</button>
              <button className={ghostCls} onClick={() => {
                const how = window.prompt("What fixed it?", "");
                if (how != null) save.mutate({ id: r.id, body: { status: "resolved", resolution: how } });
              }}>Resolve</button>
            </div>) : <span className="text-xs text-slate-500">{r.resolution}</span> },
        ]}
        rows={data}
        empty="No tickets"
      />
    </Card>
  );
}

/* ------------------------------------------------------------------ maintenance */

export function MaintenancePanel({ olts }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ olt: "", pon_port: "", starts_at: "", ends_at: "", reason: "", compensate: true });
  const { data = [] } = useQuery({ queryKey: ["fiber-maintenance"], queryFn: fetchMaintenance });
  const create = useMutation({
    mutationFn: () => createMaintenance({
      ...form, pon_port: form.pon_port || null,
      starts_at: new Date(form.starts_at).toISOString(), ends_at: new Date(form.ends_at).toISOString(),
    }),
    onSuccess: (w) => {
      qc.invalidateQueries({ queryKey: ["fiber-maintenance"] });
      toast.success(`Scheduled · ${w.notified_count} customers texted`);
      setForm({ ...form, reason: "", starts_at: "", ends_at: "" });
    },
    onError: (e) => toast.error(errText(e)),
  });
  const remove = useMutation({ mutationFn: deleteMaintenance,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["fiber-maintenance"] }) });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Plan maintenance" subtitle="Affected customers are texted now, and their time is added back afterwards if you choose" />
        <div className="grid gap-3 md:grid-cols-6">
          <select className={inputCls} value={form.olt} onChange={(e) => setForm({ ...form, olt: e.target.value })}>
            <option value="">OLT…</option>
            {olts.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <select className={inputCls} value={form.pon_port} onChange={(e) => setForm({ ...form, pon_port: e.target.value })}>
            <option value="">All ports</option>
            {[1, 2, 3, 4].map((p) => <option key={p} value={p}>PON {p}</option>)}
          </select>
          <input type="datetime-local" className={inputCls} value={form.starts_at} onChange={(e) => setForm({ ...form, starts_at: e.target.value })} aria-label="Starts" />
          <input type="datetime-local" className={inputCls} value={form.ends_at} onChange={(e) => setForm({ ...form, ends_at: e.target.value })} aria-label="Ends" />
          <input className={inputCls} placeholder="Reason (e.g. splicing)" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
          <button className={buttonCls} disabled={!form.olt || !form.starts_at || !form.ends_at || !form.reason || create.isPending}
                  onClick={() => create.mutate()}>Schedule &amp; text</button>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={form.compensate} onChange={(e) => setForm({ ...form, compensate: e.target.checked })} />
          Add the window's length back to affected customers' packages when it ends
        </label>
      </Card>
      <Card padded={false}>
        <DataTable
          columns={[
            { key: "olt", label: "Where", render: (r) => `${r.olt_name}${r.pon_port ? ` PON ${r.pon_port}` : " (all ports)"}` },
            { key: "when", label: "When", render: (r) => `${when(r.starts_at)} → ${when(r.ends_at)}` },
            { key: "reason", label: "Reason", render: (r) => r.reason },
            { key: "told", label: "Texted", align: "right", render: (r) => r.notified_count },
            { key: "comp", label: "Compensated", render: (r) => r.compensated_at ? when(r.compensated_at) : r.compensate ? "after it ends" : "no" },
            { key: "x", label: "", render: (r) => new Date(r.starts_at) > new Date() ? (
              <button className={ghostCls} onClick={() => remove.mutate(r.id)}>Cancel</button>) : null },
          ]}
          rows={data}
          empty="No maintenance planned"
        />
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ reports */

export function ReportPanel() {
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const { data } = useQuery({ queryKey: ["fiber-monthly", month], queryFn: () => fetchMonthlyReport(month) });
  const ports = useQuery({ queryKey: ["fiber-ports", 30], queryFn: () => fetchPortReport(30) });
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Monthly network report</h3>
        <input type="month" className={`${inputCls} w-auto`} value={month} onChange={(e) => setMonth(e.target.value)} />
      </div>
      {data && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-6 gap-4">
            <StatTile label="ONUs" value={num(data.totals.onus)} />
            <StatTile label="Linked customers" value={num(data.totals.customers)} />
            <StatTile label="Revenue from fiber" value={KES(data.totals.revenue)} />
            <StatTile label="Uptime" value={data.totals.uptime_pct != null ? `${data.totals.uptime_pct}%` : "—"} />
            <StatTile label="Outage minutes" value={num(data.totals.outage_minutes)} />
            <StatTile label="Compensated" value={`${num(data.compensation.grants)} grants`} sub={`${num(data.compensation.minutes)} minutes`} />
          </div>
          <p className="text-xs text-slate-400">
            Signal now: {Object.entries(data.signal_now).map(([k, v]) => `${v} ${k}`).join(" · ") || "no readings"} ·
            {" "}{data.degrading_now} sliding · tickets {data.tickets.opened} opened, {data.tickets.resolved} resolved ·
            {" "}outages by cause: {Object.entries(data.outages_by_cause).map(([k, v]) => `${v} ${CAUSE_LABEL[k] || k}`).join(", ") || "none"}
          </p>
        </>
      )}
      <Card padded={false}>
        <div className="px-5 pt-5">
          <CardHeader title="By PON port" subtitle="Last 30 days: where the customers, the money and the trouble are" />
        </div>
        <DataTable
          loading={ports.isLoading}
          columns={[
            { key: "port", label: "Port", render: (r) => `${r.olt} PON ${r.pon_port}` },
            { key: "onus", label: "ONUs", align: "right", render: (r) => r.onus },
            { key: "cust", label: "Customers", align: "right", render: (r) => r.customers },
            { key: "rev", label: "Revenue", align: "right", render: (r) => KES(r.revenue) },
            { key: "up", label: "Uptime", align: "right", render: (r) => (r.uptime_pct != null ? `${r.uptime_pct}%` : "—") },
            { key: "peak", label: "Peak load", align: "right", render: (r) => `${r.peak_mbps} Mbps (${r.peak_utilisation_pct}%)` },
            { key: "gb", label: "Traffic", align: "right", hideBelow: "md", render: (r) => `${r.rx_gb} / ${r.tx_gb} GB` },
            { key: "out", label: "Outages", align: "right", render: (r) => `${r.outages} · ${minutes(r.outage_minutes)}` },
          ]}
          rows={ports.data || []}
          rowKey={(r) => `${r.olt_id}-${r.pon_port}`}
          empty="No ports yet"
        />
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ OLTs */

const EMPTY_OLT = { name: "", vendor: "vsol", model: "", mgmt_ip: "", snmp_community: "", cli_username: "",
                    cli_password: "", transport: "snmp", pon_ports: 4, uplink_interface: "" };

export function OltPanel({ isAdmin }) {
  const qc = useQueryClient();
  const { data = [] } = useQuery({ queryKey: ["fiber-olts"], queryFn: fetchOlts });
  const [form, setForm] = useState(EMPTY_OLT);
  const [editing, setEditing] = useState(null);
  const save = useMutation({
    mutationFn: () => (editing ? updateOlt(editing, form) : createOlt(form)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fiber-olts"] }); qc.invalidateQueries({ queryKey: ["fiber-overview"] });
                       toast.success("Saved"); setForm(EMPTY_OLT); setEditing(null); },
    onError: (e) => toast.error(errText(e)),
  });
  const poll = useMutation({ mutationFn: pollOlt, onSuccess: () => toast.success("Poll queued — refresh in a minute") });
  const f = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  return (
    <div className="space-y-4">
      <Card padded={false}>
        <DataTable
          columns={[
            { key: "name", label: "OLT", render: (r) => <span className="font-medium text-white">{r.name}</span> },
            { key: "model", label: "Model", render: (r) => `${r.vendor.toUpperCase()} ${r.model}` },
            { key: "ip", label: "Management", render: (r) => <span className="font-mono text-xs">{r.mgmt_ip}</span> },
            { key: "state", label: "State", render: (r) => <StatusBadge status={r.is_reachable ? "online" : "offline"} label={r.is_reachable ? "Reachable" : r.last_ok_at ? "Not answering" : "Never reached"} /> },
            { key: "err", label: "Last error", hideBelow: "md", render: (r) => <span className="text-xs text-slate-400">{r.last_error || "—"}</span> },
            { key: "x", label: "", render: (r) => isAdmin && (
              <div className="flex gap-1">
                <button className={ghostCls} onClick={() => poll.mutate(r.id)}>Poll now</button>
                <button className={ghostCls} onClick={() => { setEditing(r.id); setForm({ ...EMPTY_OLT, ...r, snmp_community: "", cli_password: "" }); }}>Edit</button>
              </div>) },
          ]}
          rows={data}
          empty="No OLTs yet"
        />
      </Card>
      {isAdmin && (
        <Card>
          <CardHeader title={editing ? "Edit OLT" : "Add an OLT"}
                      subtitle="Billing reads it over the tunnel, read-only. Secrets are stored encrypted and never shown again; leave blank to keep them." />
          <div className="grid gap-3 md:grid-cols-3">
            <input className={inputCls} placeholder="Name (e.g. VSOL Makongeni)" value={form.name} onChange={f("name")} />
            <select className={inputCls} value={form.vendor} onChange={f("vendor")}>
              <option value="vsol">VSOL</option><option value="hioso">HiOSO</option>
            </select>
            <input className={inputCls} placeholder="Model (V1600D-MINI, HA7304)" value={form.model} onChange={f("model")} />
            <input className={inputCls} placeholder="Management IP (10.0.0.2)" value={form.mgmt_ip} onChange={f("mgmt_ip")} />
            <input className={inputCls} placeholder="SNMP read community" value={form.snmp_community} onChange={f("snmp_community")} />
            <input className={inputCls} placeholder="Uplink on the CCR (sfp-sfpplus3)" value={form.uplink_interface} onChange={f("uplink_interface")} />
            <input className={inputCls} placeholder="CLI username (optional)" value={form.cli_username} onChange={f("cli_username")} />
            <input type="password" className={inputCls} placeholder="CLI password (optional)" value={form.cli_password} onChange={f("cli_password")} />
            <div className="flex gap-2">
              <button className={buttonCls} disabled={!form.name || !form.mgmt_ip || save.isPending} onClick={() => save.mutate()}>
                {editing ? "Save" : "Add OLT"}
              </button>
              {editing && <button className={ghostCls} onClick={() => { setEditing(null); setForm(EMPTY_OLT); }}>Cancel</button>}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ splitters */

export function SplitterPanel({ isAdmin }) {
  const qc = useQueryClient();
  const { data: list = [] } = useQuery({ queryKey: ["fiber-splitters", "all"], queryFn: () => fetchSplitters() });
  const { data: sug = [], isLoading } = useQuery({ queryKey: ["fiber-splitter-suggest"], queryFn: () => fetchSplitterSuggestions() });
  const create = useMutation({
    mutationFn: (s) => createSplitter(s),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["fiber-splitters"] }); qc.invalidateQueries({ queryKey: ["fiber-splitter-suggest"] });
                       toast.success("Splitter created and ONUs assigned"); },
    onError: (e) => toast.error(errText(e)),
  });
  const accept = (g) => {
    const name = window.prompt("Name this splitter (e.g. Junction box, Makongeni)", g.name);
    if (!name) return;
    const ratio = window.prompt("Split ratio (e.g. 1:8)", "1:8") || "";
    const avg = Math.round((g.distance_from_m + g.distance_to_m) / 2);
    create.mutate({ olt: g.olt_id, pon_port: g.pon_port, name, ratio, distance_m: avg, onu_ids: g.onu_ids });
  };
  return (
    <div className="space-y-4">
      <Card padded={false}>
        <div className="px-5 pt-5">
          <CardHeader title="Suggested splitter groups"
                      subtitle="ONUs on one port at similar fiber distances, which tend to fail together. Confirm the ones that are right." />
        </div>
        <DataTable
          loading={isLoading}
          rowKey={(r) => r.onu_ids.join("-")}
          columns={[
            { key: "port", label: "Port", render: (r) => `PON ${r.pon_port}` },
            { key: "dist", label: "Distance", render: (r) => `${metres(r.distance_from_m)} – ${metres(r.distance_to_m)}` },
            { key: "n", label: "ONUs", align: "right", render: (r) => r.onu_ids.length },
            { key: "who", label: "Members", hideBelow: "md", render: (r) => <span className="text-xs text-slate-400">{r.onus.slice(0, 6).join(", ")}{r.onus.length > 6 ? "…" : ""}</span> },
            { key: "conf", label: "Confidence", render: (r) => <StatusBadge status={r.confidence === "high" ? "good" : r.confidence === "medium" ? "warning" : "unknown"} label={`${r.confidence} · ${r.failed_together_pct}% failed together`} /> },
            { key: "x", label: "", render: (r) => isAdmin && <button className={ghostCls} onClick={() => accept(r)}>Create splitter</button> },
          ]}
          rows={sug}
          empty="No ungrouped ONUs with distances yet"
        />
      </Card>
      <Card padded={false}>
        <div className="px-5 pt-5"><CardHeader title="Splitters" subtitle="Place them on the map from the Field install page or by editing" /></div>
        <DataTable
          columns={[
            { key: "name", label: "Name", render: (r) => r.name },
            { key: "port", label: "Port", render: (r) => `PON ${r.pon_port}` },
            { key: "ratio", label: "Ratio", render: (r) => r.ratio || "—" },
            { key: "dist", label: "Distance", align: "right", render: (r) => metres(r.distance_m) },
            { key: "n", label: "ONUs", align: "right", render: (r) => r.onu_count },
            { key: "map", label: "On map", render: (r) => (r.latitude != null ? "yes" : "no") },
          ]}
          rows={list}
          empty="No splitters recorded yet"
        />
      </Card>
    </div>
  );
}
