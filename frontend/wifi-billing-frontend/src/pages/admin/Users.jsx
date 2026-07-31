import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldCheck, UserX, UserCheck } from "lucide-react";
import toast from "react-hot-toast";
import AdminLayout from "../../components/admin/AdminLayout";
import ConfirmModal from "../../components/ui/ConfirmModal";
import {
  createUser,
  disableUser,
  fetchUsers,
  updateUser,
} from "../../services/account";
import { getUser } from "../../services/auth";

/**
 * An operator's own staff.
 *
 * Everything here is scoped to the caller's operator by the backend — nothing
 * on this page names a tenant, and a tenant id in the payload would be ignored.
 */
export default function Users() {
  const qc = useQueryClient();
  const me = getUser();
  const [adding, setAdding] = useState(false);
  const [confirmDisable, setConfirmDisable] = useState(null);
  const [form, setForm] = useState({ username: "", password: "", role: "tenant_staff" });
  const [errors, setErrors] = useState({});

  const { data: users = [], isLoading } = useQuery({
    queryKey: ["tenant-users"],
    queryFn: fetchUsers,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["tenant-users"] });

  const create = useMutation({
    mutationFn: createUser,
    onSuccess: (u) => {
      toast.success(`${u.username} created — they must set their own password`);
      setAdding(false);
      setForm({ username: "", password: "", role: "tenant_staff" });
      setErrors({});
      refresh();
    },
    onError: (e) => {
      const data = e.response?.data;
      if (data && typeof data === "object" && !data.detail) setErrors(data);
      else toast.error(data?.detail || "Couldn't create the user");
    },
  });

  const toggle = useMutation({
    mutationFn: ({ id, is_active }) => updateUser(id, { is_active }),
    onSuccess: () => {
      toast.success("Updated");
      refresh();
    },
    onError: (e) => toast.error(e.response?.data?.detail || "Couldn't update"),
  });

  const remove = useMutation({
    mutationFn: disableUser,
    onSuccess: () => {
      toast.success("Account disabled and signed out");
      setConfirmDisable(null);
      refresh();
    },
    onError: (e) => {
      toast.error(e.response?.data?.detail || "Couldn't disable");
      setConfirmDisable(null);
    },
  });

  const err = (k) => (Array.isArray(errors[k]) ? errors[k].join(" ") : errors[k]);

  return (
    <AdminLayout>
      <div className="max-w-4xl space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Team</h1>
            <p className="text-slate-400 text-sm mt-1">
              People who can sign in to your business
            </p>
          </div>
          <button
            onClick={() => setAdding((v) => !v)}
            className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
          >
            <Plus size={16} />
            Add someone
          </button>
        </div>

        {adding && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate(form);
            }}
            className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5 space-y-4"
          >
            <div className="grid sm:grid-cols-3 gap-4">
              <label className="block">
                <span className="text-sm font-medium text-slate-300">Username</span>
                <input
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                  autoComplete="off"
                  className={inputCls(err("username"))}
                />
                {err("username") && (
                  <span className="text-xs text-red-300 mt-1 block">{err("username")}</span>
                )}
              </label>
              <label className="block">
                <span className="text-sm font-medium text-slate-300">
                  Temporary password
                </span>
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  autoComplete="new-password"
                  className={inputCls(err("password"))}
                />
                {err("password") && (
                  <span className="text-xs text-red-300 mt-1 block">{err("password")}</span>
                )}
              </label>
              <label className="block">
                <span className="text-sm font-medium text-slate-300">Role</span>
                <select
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                  className={inputCls(err("role"))}
                >
                  <option value="tenant_staff">Staff</option>
                  <option value="tenant_admin">Admin</option>
                </select>
              </label>
            </div>
            <div className="rounded-lg bg-white/5 border border-white/10 px-4 py-3 text-xs text-slate-300 space-y-1.5">
              <p>
                <strong className="text-white">Staff</strong> run the counter:
                they can see customers, packages, invoices and the network, look
                up who has access, resend a voucher and record a cash payment.
              </p>
              <p>
                <strong className="text-white">Admin</strong> can do all of
                that and change the business — add and edit customers, set
                package prices, manage routers and stations, edit settings and
                M-Pesa, and manage this team.
              </p>
            </div>
            <p className="text-xs text-slate-500">
              Give them this password directly. They will be made to replace it
              the first time they sign in, and you will not see it again.
            </p>
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={create.isPending}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg px-4 py-2 text-sm font-semibold"
              >
                {create.isPending ? "Creating…" : "Create"}
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

        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
          {isLoading ? (
            <div className="px-5 py-10 text-center text-sm text-slate-500">Loading…</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-white/5 border-b border-white/10">
                <tr>
                  {["Person", "Role", "Status", ""].map((h) => (
                    <th
                      key={h}
                      className="px-5 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {users.map((u) => {
                  const isMe = u.id === me?.id;
                  return (
                    <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
                      <td className="px-5 py-3.5">
                        <p className="font-medium text-white">
                          {u.username}
                          {isMe && <span className="text-xs text-slate-500"> · you</span>}
                        </p>
                        {u.email && <p className="text-xs text-slate-500">{u.email}</p>}
                      </td>
                      <td className="px-5 py-3.5">
                        <span className="inline-flex items-center gap-1.5 text-slate-300">
                          {u.role === "tenant_admin" && (
                            <ShieldCheck size={13} className="text-blue-600" aria-hidden="true" />
                          )}
                          {u.role === "tenant_admin" ? "Admin" : "Staff"}
                        </span>
                      </td>
                      <td className="px-5 py-3.5">
                        {!u.is_active ? (
                          <span className="text-xs font-medium text-slate-400">Disabled</span>
                        ) : u.must_change_password ? (
                          <span className="text-xs font-medium text-amber-300">
                            Must set password
                          </span>
                        ) : (
                          <span className="text-xs font-medium text-emerald-300">Active</span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        {isMe ? (
                          <span className="text-xs text-slate-500">—</span>
                        ) : u.is_active ? (
                          <button
                            onClick={() => setConfirmDisable(u)}
                            className="inline-flex items-center gap-1.5 text-red-300 hover:text-red-300 text-xs font-semibold"
                          >
                            <UserX size={13} /> Disable
                          </button>
                        ) : (
                          <button
                            onClick={() => toggle.mutate({ id: u.id, is_active: true })}
                            className="inline-flex items-center gap-1.5 text-emerald-300 hover:text-emerald-300 text-xs font-semibold"
                          >
                            <UserCheck size={13} /> Enable
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <ConfirmModal
        open={!!confirmDisable}
        title={`Disable ${confirmDisable?.username}?`}
        description="They are signed out immediately and cannot sign in again until you enable them. Their history is kept."
        confirmText="Disable"
        danger
        onConfirm={() => remove.mutate(confirmDisable.id)}
        onCancel={() => setConfirmDisable(null)}
      />
    </AdminLayout>
  );
}

const inputCls = (hasError) =>
  `mt-1 w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
    hasError ? "border-red-300 focus:ring-red-400" : "border-white/15 bg-slate-950 text-slate-100 focus:ring-blue-500"
  }`;
