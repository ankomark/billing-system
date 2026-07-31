import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, User as UserIcon } from "lucide-react";
import toast from "react-hot-toast";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchProfile, updateProfile } from "../../services/account";
import { getUser } from "../../services/auth";

/**
 * The operator's own account.
 *
 * Before this the only self-service page was Settings, which holds nine
 * third-party credentials and nothing about the person signed in — there was no
 * way to change your own username or password anywhere in the product.
 */
export default function MyAccount() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState({});

  const { data: profile, isLoading } = useQuery({
    queryKey: ["profile"],
    queryFn: fetchProfile,
  });

  useEffect(() => {
    if (profile) {
      setUsername(profile.username || "");
      setEmail(profile.email || "");
    }
  }, [profile]);

  const mutation = useMutation({
    mutationFn: updateProfile,
    onSuccess: (updated) => {
      // The app shell reads the cached copy, so a stale one would show the old
      // username until the next sign-in.
      const cached = getUser();
      if (cached) {
        localStorage.setItem("user", JSON.stringify({ ...cached, ...updated }));
      }
      qc.invalidateQueries({ queryKey: ["profile"] });
      setErrors({});
      toast.success("Account updated");
    },
    onError: (e) => {
      const data = e.response?.data;
      if (data && typeof data === "object" && !data.detail) {
        setErrors(data);
        toast.error("Check the highlighted fields");
      } else {
        toast.error(data?.detail || "Couldn't save");
      }
    },
  });

  const err = (k) => (Array.isArray(errors[k]) ? errors[k].join(" ") : errors[k]);

  return (
    <AdminLayout>
      <div className="max-w-2xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">My account</h1>
          <p className="text-slate-400 text-sm mt-1">
            Your sign-in details. Your operator's business details are under
            Settings.
          </p>
        </div>

        {isLoading ? (
          <div className="h-40 rounded-xl border border-white/10 bg-slate-900/80 animate-pulse" />
        ) : (
          <>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                mutation.mutate({ username: username.trim(), email: email.trim() });
              }}
              className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5 space-y-4"
            >
              <div className="flex items-center gap-2">
                <UserIcon size={16} className="text-slate-500" aria-hidden="true" />
                <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">
                  Sign-in details
                </h2>
              </div>

              <label className="block">
                <span className="text-sm font-medium text-slate-300">Username</span>
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className={inputCls(err("username"))}
                />
                {err("username") && (
                  <span className="text-xs text-red-300 mt-1 block">{err("username")}</span>
                )}
              </label>

              <label className="block">
                <span className="text-sm font-medium text-slate-300">Email</span>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={inputCls(err("email"))}
                />
                {err("email") && (
                  <span className="text-xs text-red-300 mt-1 block">{err("email")}</span>
                )}
              </label>

              <div className="grid sm:grid-cols-2 gap-4 pt-2 border-t border-white/5">
                <ReadOnly label="Role" value={roleLabel(profile?.role)} />
                <ReadOnly label="Operator" value={profile?.tenant_name || "—"} />
              </div>
              <p className="text-xs text-slate-500">
                Your role is set by your operator's admin, and cannot be changed
                here.
              </p>

              <button
                type="submit"
                disabled={mutation.isPending}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
              >
                {mutation.isPending ? "Saving…" : "Save changes"}
              </button>
            </form>

            <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5">
              <div className="flex items-center gap-2 mb-2">
                <KeyRound size={16} className="text-slate-500" aria-hidden="true" />
                <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">
                  Password
                </h2>
              </div>
              <p className="text-sm text-slate-400 mb-4">
                Changing your password signs out every other session, including
                any device you no longer have.
              </p>
              <Link
                to="/change-password"
                className="inline-block border border-white/15 hover:bg-white/5 text-slate-300 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
              >
                Change password
              </Link>
            </div>
          </>
        )}
      </div>
    </AdminLayout>
  );
}

const inputCls = (hasError) =>
  `mt-1 w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
    hasError ? "border-red-300 focus:ring-red-400" : "border-white/15 bg-slate-950 text-slate-100 focus:ring-blue-500"
  }`;

const ROLE_LABELS = {
  tenant_admin: "Operator admin",
  tenant_staff: "Operator staff",
  platform_owner: "Platform owner",
  platform_staff: "Platform staff",
  customer: "Customer",
};

const roleLabel = (r) => ROLE_LABELS[r] || r || "—";

function ReadOnly({ label, value }) {
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-sm font-medium text-slate-300 mt-0.5">{value}</p>
    </div>
  );
}
