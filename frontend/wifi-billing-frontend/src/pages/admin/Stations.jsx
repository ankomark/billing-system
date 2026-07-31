import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Plus, Router as RouterIcon, Trash2, Users } from "lucide-react";
import toast from "react-hot-toast";
import AdminLayout from "../../components/admin/AdminLayout";
import ConfirmModal from "../../components/ui/ConfirmModal";
import {
  createStation,
  deleteStation,
  fetchStations,
} from "../../services/routers";

/**
 * An operator's sites.
 *
 * Deliberately thin. A station groups the hardware at a place so two towns can
 * be told apart when monitoring them — it is not a second account, and it owns
 * no billing, packages or credentials of its own. An operator with one site
 * never needs to come here.
 */
export default function Stations() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", code: "", notes: "" });
  const [errors, setErrors] = useState({});
  const [confirmDelete, setConfirmDelete] = useState(null);

  const { data: stations = [], isLoading } = useQuery({
    queryKey: ["stations"],
    queryFn: fetchStations,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["stations"] });
    qc.invalidateQueries({ queryKey: ["routers"] });
  };

  const create = useMutation({
    mutationFn: createStation,
    onSuccess: (s) => {
      toast.success(`${s.name} added`);
      setAdding(false);
      setForm({ name: "", code: "", notes: "" });
      setErrors({});
      refresh();
    },
    onError: (e) => {
      const data = e.response?.data;
      if (data && typeof data === "object" && !data.detail) setErrors(data);
      else toast.error(data?.detail || "Couldn't add the station");
    },
  });

  const remove = useMutation({
    mutationFn: deleteStation,
    onSuccess: () => {
      toast.success("Station removed");
      setConfirmDelete(null);
      refresh();
    },
    onError: (e) => {
      // The backend refuses while routers are still there, rather than
      // silently ungrouping them.
      const data = e.response?.data;
      toast.error(
        (Array.isArray(data) ? data[0] : data?.detail || data) ||
          "Couldn't remove the station"
      );
      setConfirmDelete(null);
    },
  });

  const err = (k) => (Array.isArray(errors[k]) ? errors[k].join(" ") : errors[k]);

  return (
    <AdminLayout>
      <div className="max-w-4xl space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Stations</h1>
            <p className="text-slate-400 text-sm mt-1">
              Your sites. Group the routers at each place so you can watch them
              separately.
            </p>
          </div>
          <button
            onClick={() => setAdding((v) => !v)}
            className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
          >
            <Plus size={16} />
            Add station
          </button>
        </div>

        {stations.length === 0 && !isLoading && !adding && (
          <div className="bg-blue-500/10 border border-blue-500/30 text-blue-200 rounded-xl px-5 py-4 text-sm">
            <p className="font-semibold">You don't need stations for one site.</p>
            <p className="mt-1">
              Add one only when you have routers in more than one place. Once you
              do, a customer at one site is only ever moved to another router at
              the same site — never to hardware in another town, which could not
              reach them anyway.
            </p>
          </div>
        )}

        {adding && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate(form);
            }}
            className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5 space-y-4"
          >
            <div className="grid sm:grid-cols-3 gap-4">
              <label className="block sm:col-span-2">
                <span className="text-sm font-medium text-slate-300">Name</span>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Kilifi Town"
                  className={inputCls(err("name"))}
                />
                {err("name") && (
                  <span className="text-xs text-red-300 mt-1 block">{err("name")}</span>
                )}
              </label>
              <label className="block">
                <span className="text-sm font-medium text-slate-300">
                  Short code
                </span>
                <input
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                  placeholder="KLF"
                  maxLength={12}
                  className={inputCls(err("code"))}
                />
              </label>
            </div>
            <label className="block">
              <span className="text-sm font-medium text-slate-300">Notes</span>
              <input
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                placeholder="Mast behind the market, key with James"
                className={inputCls(err("notes"))}
              />
            </label>
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={create.isPending}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg px-4 py-2 text-sm font-semibold"
              >
                {create.isPending ? "Adding…" : "Add station"}
              </button>
              <button
                type="button"
                onClick={() => setAdding(false)}
                className="text-slate-400 hover:text-white text-sm font-medium"
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {isLoading ? (
          <div className="h-32 rounded-xl border border-white/10 bg-slate-900/80 animate-pulse" />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {stations.map((s) => (
              <div
                key={s.id}
                className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-semibold text-white flex items-center gap-1.5">
                      <MapPin size={14} className="text-slate-500" aria-hidden="true" />
                      {s.name}
                      {s.code && (
                        <span className="text-xs font-normal text-slate-500">
                          {s.code}
                        </span>
                      )}
                    </p>
                    {s.notes && (
                      <p className="text-xs text-slate-400 mt-1">{s.notes}</p>
                    )}
                  </div>
                  <button
                    onClick={() => setConfirmDelete(s)}
                    className="text-slate-500 hover:text-red-300 transition-colors"
                    aria-label={`Remove ${s.name}`}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>

                <div className="mt-4 pt-4 border-t border-white/5 flex items-center gap-5 text-sm">
                  <span className="inline-flex items-center gap-1.5 text-slate-300">
                    <RouterIcon size={14} className="text-slate-500" aria-hidden="true" />
                    {s.routers} router{s.routers === 1 ? "" : "s"}
                  </span>
                  {s.routers_offline > 0 && (
                    <span className="text-red-300 text-xs font-semibold">
                      {s.routers_offline} offline
                    </span>
                  )}
                  <span className="inline-flex items-center gap-1.5 text-slate-300 ml-auto">
                    <Users size={14} className="text-slate-500" aria-hidden="true" />
                    {s.subscribers}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <ConfirmModal
        open={!!confirmDelete}
        title={`Remove ${confirmDelete?.name}?`}
        description="The routers and customers there are not deleted. If any routers are still assigned to this station, move them first — the station cannot be removed while they are."
        confirmText="Remove"
        danger
        onConfirm={() => remove.mutate(confirmDelete.id)}
        onCancel={() => setConfirmDelete(null)}
      />
    </AdminLayout>
  );
}

const inputCls = (hasError) =>
  `mt-1 w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
    hasError ? "border-red-300 focus:ring-red-400" : "border-white/15 bg-slate-950 text-slate-100 focus:ring-blue-500"
  }`;
