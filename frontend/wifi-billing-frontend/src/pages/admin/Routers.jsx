import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ClipboardCopy,
  Pencil,
  Plus,
  Plug,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";
import toast from "react-hot-toast";
import AdminLayout from "../../components/admin/AdminLayout";
import ConfirmModal from "../../components/ui/ConfirmModal";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { getUser } from "../../services/auth";
import {
  createRouter,
  deleteRouter,
  fetchRouters,
  fetchStations,
  provisionRouter,
  testRouter,
  updateRouter,
} from "../../services/routers";

/**
 * An operator's routers, and where they add one.
 *
 * Registering hardware used to mean asking the platform owner to type it into
 * the Django admin, because that was the only path that saved the API password.
 * This page is the whole job now: add it, prove the credentials work before
 * saving, and take it out of service later.
 */
export default function Routers() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(null); // null | "new" | router object
  const [confirmDelete, setConfirmDelete] = useState(null);

  // Staff read the network; admins change it. The endpoints enforce the same
  // split, so this is presentation — it just spares them buttons that answer
  // with a 403.
  const isAdmin = getUser()?.role !== "tenant_staff";

  const { data: routers = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["routers"],
    queryFn: fetchRouters,
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000,
  });

  const remove = useMutation({
    mutationFn: deleteRouter,
    onSuccess: () => {
      toast.success("Router removed");
      setConfirmDelete(null);
      qc.invalidateQueries({ queryKey: ["routers"] });
    },
    onError: (e) => {
      // The backend refuses while subscribers are still on it, rather than
      // detaching them silently.
      toast.error(e.response?.data?.detail || "Couldn't remove the router");
      setConfirmDelete(null);
    },
  });

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Routers</h1>
            <p className="text-slate-400 text-sm mt-1">
              Your hardware. Add a router, check it answers, and watch its status.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-white/15 rounded-lg text-xs font-medium text-slate-300 hover:bg-white/5 disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
              Refresh
            </button>
            {isAdmin && (
              <button
                onClick={() => setEditing(editing === "new" ? null : "new")}
                className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
              >
                <Plus size={16} />
                Add router
              </button>
            )}
          </div>
        </div>

        {isError && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-300 text-sm">
            Failed to load routers. Try refreshing.
          </div>
        )}

        {editing && (
          <RouterForm
            key={editing === "new" ? "new" : editing.id}
            router={editing === "new" ? null : editing}
            onDone={() => {
              setEditing(null);
              qc.invalidateQueries({ queryKey: ["routers"] });
            }}
            onCancel={() => setEditing(null)}
          />
        )}

        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
          {/* The card clips; this scrolls. Without it the right-hand columns
              are not merely off-screen on a phone, they are unreachable. */}
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-white/5 border-b border-white/10">
              <tr>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Name</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">IP Address</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Station</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Priority</th>
                <th className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Status</th>
                <th className="px-5 py-3 text-right text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {isLoading ? (
                <SkeletonTable rows={4} cols={6} />
              ) : routers.length === 0 ? (
                <tr>
                  <td colSpan="6" className="px-6 py-10 text-center text-slate-500 text-sm">
                    No routers yet. Add the first one to start connecting subscribers.
                  </td>
                </tr>
              ) : (
                routers.map((router) => (
                  <tr key={router.id} className="hover:bg-white/5 transition-colors">
                    <td className="px-6 py-4 font-medium text-white">
                      {router.name}
                      {/* What the box calls itself, read from it the first time
                          its credentials worked. Two names that disagree is
                          worth seeing. */}
                      {router.identity && router.identity !== router.name && (
                        <span className="block text-xs font-normal text-slate-500 mt-0.5">
                          RouterOS identity: {router.identity}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 font-mono text-xs text-slate-300">{router.ip_address}</td>
                    <td className="px-6 py-4 text-slate-300">
                      {/* Blank is the normal single-site case, not missing data. */}
                      {router.station_name || <span className="text-slate-500">—</span>}
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-blue-500/10 text-blue-300 border border-blue-500/30">
                        Priority {router.priority}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      {!router.is_active ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-500/10 text-slate-300 border border-slate-500/30">
                          Out of service
                        </span>
                      ) : router.is_online ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/100"></span>
                          Online
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-red-500/10 text-red-300 border border-red-500/30">
                          <span className="w-1.5 h-1.5 rounded-full bg-red-500/100"></span>
                          Offline
                        </span>
                      )}
                      {!router.has_password && (
                        <span className="block text-xs text-amber-300 mt-1">
                          No API password set
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center justify-end gap-3">
                        {isAdmin ? (
                          <>
                            <TestButton router={router} />
                            <button
                              onClick={() => setEditing(router)}
                              className="text-slate-500 hover:text-white transition-colors"
                              aria-label={`Edit ${router.name}`}
                            >
                              <Pencil size={15} />
                            </button>
                            <button
                              onClick={() => setConfirmDelete(router)}
                              className="text-slate-500 hover:text-red-300 transition-colors"
                              aria-label={`Remove ${router.name}`}
                            >
                              <Trash2 size={15} />
                            </button>
                          </>
                        ) : (
                          <span className="text-slate-600 text-xs">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          </div>
        </div>
      </div>

      <ConfirmModal
        open={!!confirmDelete}
        title={`Remove ${confirmDelete?.name}?`}
        description="This deletes the router from the platform. Subscribers still assigned to it must be moved first — if any are, the removal is refused. To take hardware out of service without deleting it, edit it and clear 'In service' instead."
        confirmText="Remove"
        danger
        onConfirm={() => remove.mutate(confirmDelete.id)}
        onCancel={() => setConfirmDelete(null)}
      />
    </AdminLayout>
  );
}

/**
 * Re-test a saved router.
 *
 * Sends only the id: the stored password never comes back to the browser, so
 * the backend uses what it already has.
 */
function TestButton({ router }) {
  const qc = useQueryClient();
  const test = useMutation({
    mutationFn: () => testRouter({ router_id: router.id }),
    onSuccess: (r) => {
      if (r.ok) toast.success(r.detail);
      else toast.error(r.detail, { duration: 8000 });
      qc.invalidateQueries({ queryKey: ["routers"] });
    },
    onError: (e) =>
      toast.error(e.response?.data?.detail || "Couldn't reach the router"),
  });

  return (
    <button
      onClick={() => test.mutate()}
      disabled={test.isPending}
      className="text-slate-500 hover:text-blue-300 disabled:opacity-50 transition-colors"
      aria-label={`Test connection to ${router.name}`}
      title="Test connection"
    >
      <Plug size={15} className={test.isPending ? "animate-pulse" : ""} />
    </button>
  );
}

const BLANK = {
  name: "",
  ip_address: "",
  username: "",
  password: "",
  api_port: 8728,
  priority: 1,
  max_pppoe_sessions: 0,
  station: "",
  is_active: true,
};

function RouterForm({ router, onDone, onCancel }) {
  const isEdit = !!router;
  const [form, setForm] = useState(
    isEdit
      ? {
          ...BLANK,
          ...router,
          // Never prefilled — it is not sent to the browser, and a blank box
          // here means "leave the stored one alone".
          password: "",
          station: router.station ?? "",
        }
      : BLANK
  );
  const [errors, setErrors] = useState({});
  const [probe, setProbe] = useState(null);

  // Nearly every router these operators run is behind CGNAT — an LTE box, a
  // shared uplink — so it has no address the platform could dial. Default to
  // building a tunnel, and let the rare operator with a real public IP turn it
  // off, rather than the other way round: the common case should not be the
  // one that needs a decision.
  const [useTunnel, setUseTunnel] = useState(!isEdit);

  // The RouterOS block, once provisioned. Held only here — it carries the
  // router's private key and the API password, and the backend keeps neither.
  const [setup, setSetup] = useState(null);

  const set = (k) => (e) => {
    const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  const { data: stations = [] } = useQuery({
    queryKey: ["stations"],
    queryFn: fetchStations,
  });

  const payload = () => {
    const out = {
      name: form.name.trim(),
      ip_address: form.ip_address.trim(),
      username: form.username.trim(),
      api_port: Number(form.api_port) || 8728,
      priority: Number(form.priority) || 1,
      max_pppoe_sessions: Number(form.max_pppoe_sessions) || 0,
      station: form.station === "" ? null : Number(form.station),
      is_active: !!form.is_active,
    };
    // Omitted rather than sent blank when editing, so the stored password
    // survives an edit that was only meant to change the priority.
    if (form.password) out.password = form.password;
    return out;
  };

  const onError = (e) => {
    const data = e.response?.data;
    if (data && typeof data === "object" && !data.detail) setErrors(data);
    else toast.error(data?.detail || "Couldn't save the router");
  };

  const save = useMutation({
    mutationFn: () =>
      isEdit ? updateRouter(router.id, payload()) : createRouter(payload()),
    onSuccess: (r) => {
      toast.success(isEdit ? `${r.name} updated` : `${r.name} added`);
      onDone();
    },
    onError,
  });

  /**
   * Register the router, allocate it a tunnel address, and get the script.
   *
   * Deliberately does not call onDone() — the operator still has to paste the
   * result into WinBox, and closing the form would take the one copy of the
   * private key with it.
   */
  const provision = useMutation({
    mutationFn: () => {
      const { ip_address, ...rest } = payload();
      return provisionRouter({ ...rest, password: form.password });
    },
    onSuccess: (data) => {
      setSetup(data);
      setForm((f) => ({ ...f, ip_address: data.tunnel_ip }));
      toast.success(`${data.router.name} registered at ${data.tunnel_ip}`);
    },
    onError,
  });

  const copyScript = async () => {
    try {
      await navigator.clipboard.writeText(setup.script);
      toast.success("Copied — paste it into WinBox → New Terminal");
    } catch {
      // Clipboard access is refused in some browsers over plain http, and on
      // a locked-down work laptop. The textarea below is selectable, so this
      // is a nuisance rather than a dead end.
      toast.error("Couldn't copy. Select the text and copy it by hand.");
    }
  };

  const test = useMutation({
    mutationFn: () =>
      testRouter({
        // An existing router with the password box left blank still needs
        // credentials to test with, and the id is how the backend finds them.
        ...(isEdit && !form.password ? { router_id: router.id } : {}),
        ip_address: form.ip_address.trim(),
        username: form.username.trim(),
        password: form.password,
        api_port: Number(form.api_port) || 8728,
      }),
    onSuccess: (r) => setProbe(r),
    onError: (e) =>
      setProbe({
        ok: false,
        detail: e.response?.data?.detail || "Couldn't test the connection.",
      }),
  });

  const err = (k) => (Array.isArray(errors[k]) ? errors[k].join(" ") : errors[k]);
  const canTest = form.ip_address.trim() && form.username.trim();

  // Provisioning is for new hardware only. An existing router already has an
  // address and, if it needed one, a peer — re-running it would allocate a
  // second address and leave the first in the server's config for good.
  const tunnelMode = useTunnel && !isEdit;
  const awaitingSetup = tunnelMode && !setup;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setErrors({});
        if (awaitingSetup) provision.mutate();
        else if (!setup) save.mutate();
        else onDone();
      }}
      className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5 space-y-4"
    >
      <h2 className="text-white font-semibold">
        {isEdit ? `Edit ${router.name}` : "Add a router"}
      </h2>

      {!isEdit && (
        <label className="flex items-start gap-2 text-sm text-slate-300 rounded-lg border border-white/10 bg-slate-950/60 px-3 py-2.5">
          <input
            type="checkbox"
            checked={useTunnel}
            onChange={(e) => setUseTunnel(e.target.checked)}
            disabled={!!setup}
            className="mt-0.5 rounded border-white/20 bg-slate-950"
          />
          <span>
            Set up a management tunnel
            <span className="block text-xs text-slate-500 mt-0.5">
              For a router with no public IP address — an LTE box, a shared
              uplink, anything behind CGNAT. We allocate its address, make its
              keys, and give you one block to paste into WinBox. Leave this off
              only if the router already has a public address you can reach.
            </span>
          </span>
        </label>
      )}

      <div className="grid sm:grid-cols-2 gap-4">
        <label className="block">
          <span className="text-sm font-medium text-slate-300">Name</span>
          <input
            value={form.name}
            onChange={set("name")}
            placeholder="Kilifi Core"
            className={inputCls(err("name"))}
          />
          <FieldError text={err("name")} />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">
            IP address
            {tunnelMode && (
              <span className="text-slate-500 font-normal"> (allocated for you)</span>
            )}
          </span>
          <input
            value={form.ip_address}
            onChange={set("ip_address")}
            readOnly={tunnelMode}
            placeholder={tunnelMode ? "Assigned when you register it" : "192.168.88.1"}
            className={`${inputCls(err("ip_address"))} ${
              tunnelMode ? "opacity-60 cursor-not-allowed" : ""
            }`}
          />
          <FieldError text={err("ip_address")} />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">API username</span>
          <input
            value={form.username}
            onChange={set("username")}
            placeholder="admin"
            autoComplete="off"
            className={inputCls(err("username"))}
          />
          <FieldError text={err("username")} />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">API password</span>
          <input
            type="password"
            value={form.password}
            onChange={set("password")}
            placeholder={isEdit ? "Leave blank to keep the current one" : ""}
            autoComplete="new-password"
            className={inputCls(err("password"))}
          />
          <FieldError text={err("password")} />
          {isEdit && !router.has_password && (
            <span className="text-xs text-amber-300 mt-1 block">
              This router has no password stored, so the platform cannot log in
              to it.
            </span>
          )}
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">API port</span>
          <input
            type="number"
            value={form.api_port}
            onChange={set("api_port")}
            className={inputCls(err("api_port"))}
          />
          <FieldError text={err("api_port")} />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">
            Priority <span className="text-slate-500 font-normal">(1 is used first)</span>
          </span>
          <input
            type="number"
            min={1}
            value={form.priority}
            onChange={set("priority")}
            className={inputCls(err("priority"))}
          />
          <FieldError text={err("priority")} />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">Station</span>
          <select
            value={form.station ?? ""}
            onChange={set("station")}
            className={inputCls(err("station"))}
          >
            <option value="">No station</option>
            {stations.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <FieldError text={err("station")} />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-300">
            Max PPPoE sessions{" "}
            <span className="text-slate-500 font-normal">(0 = unlimited)</span>
          </span>
          <input
            type="number"
            min={0}
            value={form.max_pppoe_sessions}
            onChange={set("max_pppoe_sessions")}
            className={inputCls(err("max_pppoe_sessions"))}
          />
          <FieldError text={err("max_pppoe_sessions")} />
        </label>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={!!form.is_active}
          onChange={set("is_active")}
          className="rounded border-white/20 bg-slate-950"
        />
        In service — subscribers may be placed on this router
      </label>

      {setup && (
        <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold text-blue-100">
              Paste this into WinBox → New Terminal
            </p>
            <button
              type="button"
              onClick={copyScript}
              className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-3 py-1.5 text-xs font-semibold"
            >
              <ClipboardCopy size={13} />
              Copy
            </button>
          </div>

          <textarea
            readOnly
            value={setup.script}
            rows={14}
            onFocus={(e) => e.target.select()}
            className="w-full font-mono text-[11px] leading-relaxed rounded-lg border border-white/10 bg-slate-950 text-slate-200 p-3"
          />

          <p className="text-xs text-blue-200/80">
            Then press <strong>Test connection</strong>. It dials{" "}
            <code className="font-mono">{setup.tunnel_ip}</code> over the tunnel
            and tells you whether the paste took.
          </p>
          <p className="text-xs text-amber-300/90">
            Shown once. This contains the router's private key and its API
            password, and neither is stored here — copy it now, or register the
            router again to get a new one.
          </p>
        </div>
      )}

      {probe && (
        <div
          className={`rounded-lg px-4 py-3 text-sm border ${
            probe.ok
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-200"
              : "bg-red-500/10 border-red-500/30 text-red-200"
          }`}
        >
          <p className="flex items-center gap-2 font-medium">
            {probe.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
            {probe.detail}
          </p>
          {probe.ok && (probe.identity || probe.serial_number) && (
            <p className="mt-1 text-xs opacity-80">
              {probe.identity && <>Identity: {probe.identity}. </>}
              {probe.serial_number && <>Serial: {probe.serial_number}.</>}
            </p>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={save.isPending || provision.isPending}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg px-4 py-2 text-sm font-semibold"
        >
          {provision.isPending
            ? "Registering…"
            : save.isPending
            ? "Saving…"
            : setup
            ? "Done"
            : awaitingSetup
            ? "Register & get setup commands"
            : isEdit
            ? "Save changes"
            : "Add router"}
        </button>
        <button
          type="button"
          onClick={() => test.mutate()}
          disabled={!canTest || test.isPending}
          className="inline-flex items-center gap-2 border border-white/15 text-slate-200 hover:bg-white/5 disabled:opacity-50 rounded-lg px-4 py-2 text-sm font-medium"
        >
          <Plug size={14} className={test.isPending ? "animate-pulse" : ""} />
          {test.isPending ? "Connecting…" : "Test connection"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-slate-400 hover:text-white text-sm font-medium"
        >
          Cancel
        </button>
      </div>

      <p className="text-xs text-slate-500">
        {awaitingSetup
          ? "Nothing needs setting up on the router first — register it here and you'll get the commands that do it, tunnel included."
          : "The router needs its RouterOS API service enabled and reachable from this platform. Testing before you save tells you which of those is missing while you can still fix it."}
      </p>
    </form>
  );
}

function FieldError({ text }) {
  if (!text) return null;
  return <span className="text-xs text-red-300 mt-1 block">{text}</span>;
}

const inputCls = (hasError) =>
  `mt-1 w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
    hasError
      ? "border-red-300 focus:ring-red-400"
      : "border-white/15 bg-slate-950 text-slate-100 focus:ring-blue-500"
  }`;
