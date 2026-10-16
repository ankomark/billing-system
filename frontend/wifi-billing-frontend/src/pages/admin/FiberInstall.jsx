import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { CheckCircle2, MapPin } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Card, Note, PageHeader } from "../../components/admin/ui";
import { fetchOnus, fetchSplitters, locateOnu } from "../../services/fiber";
import { Band, OnuStatus, buttonCls, dbm, inputCls, metres } from "../../components/admin/fiber/shared";

/**
 * The technician's page, made for a phone at a customer's door.
 *
 * Pick the ONU just installed (newest and unplaced first), confirm its light
 * is good, press one button. About thirty seconds, and the field map is built
 * one install at a time instead of in a survey nobody has time for.
 */
export default function FiberInstall() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState(null);
  const [note, setNote] = useState("");
  const [label, setLabel] = useState("");
  const [splitter, setSplitter] = useState("");
  const { data: onus = [] } = useQuery({
    queryKey: ["fiber-unplaced", q],
    queryFn: () => fetchOnus({ unlocated: 1, q: q || undefined, limit: 50 }),
  });
  const { data: splitters = [] } = useQuery({
    queryKey: ["fiber-splitters", picked?.olt_id],
    queryFn: () => fetchSplitters(picked.olt_id),
    enabled: !!picked,
  });
  const save = useMutation({
    mutationFn: (pos) => locateOnu(picked.id, { ...pos, note, label: label || undefined, splitter: splitter || undefined }),
    onSuccess: () => {
      toast.success("Saved to the map");
      qc.invalidateQueries({ queryKey: ["fiber-unplaced"] });
      setPicked(null); setNote(""); setLabel(""); setSplitter("");
    },
    onError: () => toast.error("Could not save"),
  });
  const here = () => {
    if (!navigator.geolocation) return toast.error("This phone cannot give a location");
    navigator.geolocation.getCurrentPosition(
      (p) => save.mutate({ latitude: p.coords.latitude, longitude: p.coords.longitude }),
      () => toast.error("Allow location access and try again"),
      { enableHighAccuracy: true, timeout: 20000 },
    );
  };
  const sorted = [...onus].sort((a, b) => new Date(b.first_seen) - new Date(a.first_seen));

  return (
    <AdminLayout>
      <div className="mx-auto max-w-lg space-y-4">
        <PageHeader title="Field install" subtitle="Put a new ONU on the map in about thirty seconds" />
        {!picked ? (
          <Card>
            <input className={inputCls} placeholder="Search MAC, label or customer phone" value={q} onChange={(e) => setQ(e.target.value)} />
            <ul className="mt-3 divide-y divide-white/5">
              {sorted.map((o) => (
                <li key={o.id}>
                  <button className="flex w-full items-center justify-between py-3 text-left" onClick={() => { setPicked(o); setLabel(o.label); }}>
                    <span>
                      <span className="block text-sm font-medium text-white">{o.label}</span>
                      <span className="block text-xs text-slate-400">{o.olt} {o.position} · {o.mac}</span>
                    </span>
                    <span className="text-right text-xs text-slate-400">{dbm(o.rx_power_dbm)}<br />{metres(o.distance_m)}</span>
                  </button>
                </li>
              ))}
              {!sorted.length && <li className="py-6 text-center text-sm text-slate-500">Every ONU is on the map</li>}
            </ul>
          </Card>
        ) : (
          <Card>
            <p className="text-lg font-semibold text-white">{picked.olt} {picked.position}</p>
            <p className="text-xs text-slate-400">{picked.mac}</p>
            <div className="my-4 flex items-center gap-3">
              <OnuStatus status={picked.status} />
              {picked.status === "online" && <Band band={picked.band} />}
              <span className="text-sm text-slate-300">{dbm(picked.rx_power_dbm)} · {metres(picked.distance_m)}</span>
            </div>
            {picked.band !== "good" && picked.status === "online" && (
              <Note tone="warning" title="Light is not good yet">
                Clean both connectors and check the drop cable for tight bends before you leave.
              </Note>
            )}
            <div className="mt-4 space-y-3">
              <input className={inputCls} placeholder="Name (e.g. Mama Njeri)" value={label} onChange={(e) => setLabel(e.target.value)} />
              <input className={inputCls} placeholder="Landmark (e.g. blue gate opposite church)" value={note} onChange={(e) => setNote(e.target.value)} />
              <select className={inputCls} value={splitter} onChange={(e) => setSplitter(e.target.value)}>
                <option value="">Splitter (optional)</option>
                {splitters.filter((s) => s.pon_port === picked.pon_port).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              <button className={`${buttonCls} w-full justify-center py-3 text-base`} onClick={here} disabled={save.isPending}>
                {save.isPending ? <CheckCircle2 size={18} /> : <MapPin size={18} />} Save where I am now
              </button>
              <button className="w-full text-center text-xs text-slate-400" onClick={() => setPicked(null)}>Pick a different ONU</button>
            </div>
          </Card>
        )}
      </div>
    </AdminLayout>
  );
}
