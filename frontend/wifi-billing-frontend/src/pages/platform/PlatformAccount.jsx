import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, ShieldAlert, User as UserIcon } from "lucide-react";
import toast from "react-hot-toast";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { Card, CardHeader, PageHeader } from "../../components/platform/ui";
import { fetchProfile, updateProfile } from "../../services/account";
import { getUser } from "../../services/auth";

/**
 * The platform owner's own account.
 *
 * The change-password route has existed since accounts landed and worked for
 * any signed-in user, including this one — but nothing in the platform console
 * ever linked to it, so the owner had no way to reach it. Someone who thought
 * their account was compromised had to know a URL.
 */
export default function PlatformAccount() {
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

  const save = useMutation({
    mutationFn: updateProfile,
    onSuccess: (updated) => {
      // The shell reads the cached copy, so a stale one would keep showing the
      // old username until the next sign-in.
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
    <PlatformLayout>
      <div className="max-w-2xl space-y-6">
        <PageHeader title="My account" subtitle="Your own sign-in details" />

        {isLoading ? (
          <div className="h-40 rounded-xl border border-white/10 bg-slate-900/60 animate-pulse" />
        ) : (
          <>
            <Card>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  save.mutate({ username: username.trim(), email: email.trim() });
                }}
                className="space-y-4"
              >
                <CardHeader
                  title="Sign-in details"
                  subtitle={`Signed in as ${profile?.role?.replace("_", " ") || "platform"}`}
                />

                <label className="block">
                  <span className="text-sm font-medium text-slate-300">Username</span>
                  <input
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className={inputCls(err("username"))}
                  />
                  {err("username") && (
                    <span className="mt-1 block text-xs text-red-300">{err("username")}</span>
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
                    <span className="mt-1 block text-xs text-red-300">{err("email")}</span>
                  )}
                </label>

                <button
                  type="submit"
                  disabled={save.isPending}
                  className="rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-teal-400 disabled:opacity-60"
                >
                  {save.isPending ? "Saving…" : "Save changes"}
                </button>
              </form>
            </Card>

            <Card>
              <CardHeader
                title="Password"
                subtitle="Change it if you think somebody else has it"
              />
              {/* The reason someone comes to this page in a hurry. */}
              <div className="mb-4 flex gap-3 rounded-lg border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
                <ShieldAlert size={16} className="mt-0.5 flex-shrink-0" aria-hidden="true" />
                <p>
                  Changing your password signs out every other session
                  immediately — including whoever you are worried about. Your own
                  session here stays signed in.
                </p>
              </div>
              <Link
                to="/change-password"
                className="inline-flex items-center gap-2 rounded-lg border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition-colors hover:bg-white/5"
              >
                <KeyRound size={15} aria-hidden="true" />
                Change my password
              </Link>
            </Card>

            <Card>
              <CardHeader title="What this account can do" />
              <ul className="space-y-1.5 text-sm text-slate-400">
                <li className="flex items-start gap-2">
                  <UserIcon size={14} className="mt-0.5 text-slate-500" aria-hidden="true" />
                  Onboard operators, set their plans and their M-Pesa
                </li>
                <li className="flex items-start gap-2">
                  <UserIcon size={14} className="mt-0.5 text-slate-500" aria-hidden="true" />
                  Reset an operator's password and view as them, which is audited
                </li>
                <li className="flex items-start gap-2">
                  <UserIcon size={14} className="mt-0.5 text-slate-500" aria-hidden="true" />
                  Restrict, close and permanently delete a closed operator
                </li>
              </ul>
            </Card>
          </>
        )}
      </div>
    </PlatformLayout>
  );
}

const inputCls = (hasError) =>
  `mt-1 w-full rounded-lg border px-3 py-2 text-sm bg-slate-950 text-slate-100 focus:outline-none focus:ring-2 ${
    hasError ? "border-red-500/40 focus:ring-red-400" : "border-white/15 focus:ring-teal-500"
  }`;
