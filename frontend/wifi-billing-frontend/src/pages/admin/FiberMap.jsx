import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import AdminLayout from "../../components/admin/AdminLayout";
import { Card, Note, PageHeader } from "../../components/admin/ui";
import { fetchFiberMap } from "../../services/fiber";
import { BAND_LABEL, dbm, metres } from "../../components/admin/fiber/shared";

// Homa Bay, for an estate with nothing placed yet.
const FALLBACK = [-0.527, 34.457];

const COLOURS = {
  outage: "#ef4444", offline: "#ef4444", critical: "#f43f5e", warning: "#f59e0b",
  good: "#22c55e", overload: "#d946ef", unknown: "#94a3b8",
};

const stateOf = (o) => (o.in_outage ? "outage" : o.status === "offline" ? "offline" : o.band);

/**
 * The plant on a map.
 *
 * Positions are captured at install (the technician page); the OLT measures
 * fiber, not geography, so an ONU nobody has placed is listed beside the map
 * rather than guessed onto it. Lines run OLT -> splitter -> ONU where both
 * ends are placed: a sketch of the plant, not a survey of the cable route.
 */
export default function FiberMap() {
  const navigate = useNavigate();
  const box = useRef(null);
  const map = useRef(null);
  const layer = useRef(null);
  const [only, setOnly] = useState("");
  const { data } = useQuery({ queryKey: ["fiber-map"], queryFn: fetchFiberMap, refetchInterval: 60 * 1000 });

  const onus = useMemo(() => (data?.onus || []).filter((o) => !only || stateOf(o) === only ||
    (only === "problem" && ["outage", "offline", "critical", "warning"].includes(stateOf(o)))), [data, only]);
  // Memoised: the drawing effect depends on it, and a fresh array every
  // render would redraw every marker on every keystroke.
  const placed = useMemo(() => onus.filter((o) => o.latitude != null), [onus]);
  const unplaced = useMemo(() => (data?.onus || []).filter((o) => o.latitude == null), [data]);

  useEffect(() => {
    if (!box.current || map.current) return;
    map.current = L.map(box.current, { zoomControl: true }).setView(FALLBACK, 14);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map.current);
    layer.current = L.layerGroup().addTo(map.current);
    return () => { map.current.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    if (!map.current || !data) return;
    const g = layer.current;
    g.clearLayers();
    const pts = [];
    const splitters = Object.fromEntries(data.splitters.map((s) => [s.id, s]));
    const olts = Object.fromEntries(data.olts.map((o) => [o.id, o]));

    data.splitters.forEach((s) => {
      if (s.lat == null) return;
      const olt = olts[s.olt_id];
      const parent = s.parent && splitters[s.parent];
      const from = parent?.lat != null ? [parent.lat, parent.lng] : olt?.lat != null ? [olt.lat, olt.lng] : null;
      if (from) L.polyline([from, [s.lat, s.lng]], { color: "#60a5fa", weight: 3, opacity: 0.7 }).addTo(g);
      L.marker([s.lat, s.lng], { icon: L.divIcon({ className: "", html:
        '<div style="width:14px;height:14px;background:#3b82f6;border:2px solid #fff;transform:rotate(45deg)"></div>' }) })
        .bindTooltip(`Splitter ${s.name}${s.ratio ? ` (${s.ratio})` : ""} · PON ${s.pon_port}`).addTo(g);
      pts.push([s.lat, s.lng]);
    });
    data.olts.forEach((o) => {
      if (o.lat == null) return;
      L.circleMarker([o.lat, o.lng], { radius: 11, color: "#fff", weight: 2, fillColor: o.reachable ? "#2563eb" : "#ef4444", fillOpacity: 1 })
        .bindTooltip(`OLT ${o.name}${o.reachable ? "" : " — not answering"}`).addTo(g);
      pts.push([o.lat, o.lng]);
    });
    placed.forEach((o) => {
      const s = o.splitter_id && splitters[o.splitter_id];
      if (s?.lat != null) L.polyline([[s.lat, s.lng], [o.latitude, o.longitude]], { color: "#94a3b8", weight: 1.5, opacity: 0.6 }).addTo(g);
      const st = stateOf(o);
      L.circleMarker([o.latitude, o.longitude], {
        radius: st === "outage" || st === "offline" ? 8 : 6, color: "#0f172a", weight: 1,
        fillColor: COLOURS[st] || COLOURS.unknown, fillOpacity: 0.95,
      })
        .bindTooltip(`<b>${o.label}</b><br/>${o.olt} ${o.position} · ${o.status}<br/>${dbm(o.rx_power_dbm)} · ${metres(o.distance_m)}${o.in_outage ? "<br/><b>In an outage</b>" : ""}`)
        .on("click", () => navigate(`/admin/fiber/onus/${o.id}`))
        .addTo(g);
      pts.push([o.latitude, o.longitude]);
    });
    if (pts.length) map.current.fitBounds(pts, { padding: [30, 30], maxZoom: 17 });
  }, [data, placed, navigate]);

  return (
    <AdminLayout>
      <div className="space-y-4">
        <PageHeader title="Fiber map" subtitle={`${placed.length} ONUs placed · ${unplaced.length} not placed yet`}>
          <select value={only} onChange={(e) => setOnly(e.target.value)}
                  className="rounded-lg border border-white/15 bg-slate-950 px-3 py-1.5 text-xs text-slate-100">
            <option value="">Everything</option>
            <option value="problem">Problems only</option>
            <option value="outage">In an outage</option>
            <option value="critical">Critical signal</option>
            <option value="warning">Weak signal</option>
          </select>
        </PageHeader>
        <div className="flex flex-wrap gap-3 text-xs text-slate-300">
          {[["outage", "In outage / offline"], ["critical", BAND_LABEL.critical], ["warning", BAND_LABEL.warning],
            ["good", BAND_LABEL.good], ["unknown", BAND_LABEL.unknown]].map(([k, l]) => (
            <span key={k} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: COLOURS[k] }} /> {l}
            </span>
          ))}
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2.5 w-2.5 rotate-45 bg-blue-500" /> Splitter</span>
        </div>
        <div ref={box} className="h-[65vh] w-full rounded-xl border border-white/10 overflow-hidden" />
        {unplaced.length > 0 && (
          <Card>
            <Note tone="info" title={`${unplaced.length} ONUs are not on the map`}>
              Open each one on a phone at the customer's door and press "Save where I am now", or use the
              Field install page, which lists them.
            </Note>
          </Card>
        )}
      </div>
    </AdminLayout>
  );
}
