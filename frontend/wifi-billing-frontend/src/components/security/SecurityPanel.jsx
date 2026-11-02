import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, LogOut, History } from "lucide-react";
import toast from "react-hot-toast";
import api from "../../services/api";
import { confirmMfaSetup, getUser, logoutEverywhere, startMfaSetup } from "../../services/auth";

const card = "rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5";
const heading = "text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]";
const btn =
  "rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-60";
const primary = `${btn} bg-blue-600 hover:bg-blue-700 text-white`;
const secondary = `${btn} border border-white/15 text-slate-300 hover:bg-white/5`;
const danger = `${btn} border border-red-400/30 text-red-300 hover:bg-red-500/10`;
const input =
  "w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500";

const detail = (err, fallback) => err?.response?.data?.detail || fallback;

/**
 * Account → Security: two-step sign-in, signing out everywhere, and the
 * record of recent sign-ins. Shared by the operator and platform account
 * pages.
 */
export default function SecurityPanel() {
  return (
    <>
      <TwoStep />
      <SignOutEverywhere />
      <SignInHistory />
    </>
  );
}

function TwoStep() {
  const qc = useQueryClient();
  const { data: status, isLoading } = useQuery({
    queryKey: ["mfa-status"],
    queryFn: () => api.get("auth/mfa/").then((r) => r.data),
  });
  const [mode, setMode] = useState(null);   // null | setup | codes | disable
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [freshCodes, setFreshCodes] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => qc.invalidateQueries({ queryKey: ["mfa-status"] });
  const reset = () => {
    setMode(null);
    setSetup(null);
    setCode("");
    setPassword("");
  };

  const act = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      toast.error(detail(err, "That did not work. Check the code and try again."));
    } finally {
      setBusy(false);
    }
  };

  const begin = () =>
    act(async () => {
      setSetup(await startMfaSetup());
      setMode("setup");
    });

  const confirm = (e) => {
    e.preventDefault();
    act(async () => {
      const data = await confirmMfaSetup(code.trim());
      setFreshCodes(data.backup_codes);
      reset();
      refresh();
      toast.success("Two-step sign-in is on.");
    });
  };

  const newCodes = (e) => {
    e.preventDefault();
    act(async () => {
      const res = await api.post("auth/mfa/backup-codes/", { code: code.trim() });
      setFreshCodes(res.data.backup_codes);
      reset();
      refresh();
    });
  };

  const disable = (e) => {
    e.preventDefault();
    act(async () => {
      await api.post("auth/mfa/disable/", { password, code: code.trim() });
      reset();
      refresh();
      toast.success("Two-step sign-in is off.");
    });
  };

  return (
    <div className={card}>
      <div className="mb-2 flex items-center gap-2">
        <ShieldCheck size={16} className="text-slate-500" aria-hidden="true" />
        <h2 className={heading}>Two-step sign-in</h2>
      </div>

      {isLoading ? (
        <div className="h-10 animate-pulse rounded bg-white/5" />
      ) : (
        <>
          <p className="mb-4 text-sm text-slate-400">
            {status?.enabled ? (
              <>
                <span className="font-semibold text-emerald-400">On.</span> Signing in
                needs your password and a code from your authenticator app.{" "}
                {status.backup_codes_left} backup code
                {status.backup_codes_left === 1 ? "" : "s"} left.
              </>
            ) : (
              <>
                <span className="font-semibold text-amber-300">Off.</span> Anyone with
                your password can sign in as you. Turn it on so they would also need
                your phone.
              </>
            )}
            {status?.required && " Required for your role."}
          </p>

          {freshCodes && (
            <div className="mb-4 rounded-lg border border-amber-400/30 bg-amber-500/10 p-4">
              <p className="mb-2 text-sm font-semibold text-amber-200">
                Your backup codes — save them now, they will not be shown again.
              </p>
              <ul className="grid grid-cols-2 gap-1 font-mono text-sm text-slate-100">
                {freshCodes.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  className={secondary}
                  onClick={() => navigator.clipboard?.writeText(freshCodes.join("\n"))}
                >
                  Copy
                </button>
                <button type="button" className={secondary} onClick={() => setFreshCodes(null)}>
                  I've saved them
                </button>
              </div>
            </div>
          )}

          {mode === null && (
            <div className="flex flex-wrap gap-2">
              {!status?.enabled && (
                <button type="button" className={primary} disabled={busy} onClick={begin}>
                  Set up two-step sign-in
                </button>
              )}
              {status?.enabled && (
                <button type="button" className={secondary} onClick={() => setMode("codes")}>
                  Make new backup codes
                </button>
              )}
              {status?.enabled && !status?.required && (
                <button type="button" className={danger} onClick={() => setMode("disable")}>
                  Turn off
                </button>
              )}
            </div>
          )}

          {mode === "setup" && setup && (
            <form onSubmit={confirm} className="space-y-3">
              <p className="text-sm text-slate-400">
                Scan this with Google Authenticator, Microsoft Authenticator or Authy,
                then type the 6-digit code it shows.
              </p>
              <img
                src={setup.qr}
                alt="QR code for your authenticator app"
                className="h-44 w-44 rounded-lg bg-white p-1"
              />
              <p className="text-xs text-slate-500">
                Or enter this key:{" "}
                <code className="select-all break-all font-mono text-slate-300">{setup.secret}</code>
              </p>
              <CodeField value={code} onChange={setCode} />
              <FormButtons busy={busy} label="Turn on" onCancel={reset} disabled={!code.trim()} />
            </form>
          )}

          {mode === "codes" && (
            <form onSubmit={newCodes} className="space-y-3">
              <p className="text-sm text-slate-400">
                Your old backup codes stop working. Enter a code from your app to continue.
              </p>
              <CodeField value={code} onChange={setCode} />
              <FormButtons busy={busy} label="Make new codes" onCancel={reset} disabled={!code.trim()} />
            </form>
          )}

          {mode === "disable" && (
            <form onSubmit={disable} className="space-y-3">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Your password"
                autoComplete="current-password"
                className={input}
              />
              <CodeField value={code} onChange={setCode} />
              <FormButtons
                busy={busy}
                label="Turn off two-step"
                onCancel={reset}
                disabled={!code.trim() || !password}
              />
            </form>
          )}
        </>
      )}
    </div>
  );
}

function CodeField({ value, onChange }) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      inputMode="numeric"
      autoComplete="one-time-code"
      placeholder="123456"
      aria-label="Authenticator code"
      className={`${input} max-w-[12rem] tracking-[0.3em]`}
    />
  );
}

function FormButtons({ busy, label, onCancel, disabled }) {
  return (
    <div className="flex gap-2">
      <button type="submit" className={primary} disabled={busy || disabled}>
        {busy ? "Checking…" : label}
      </button>
      <button type="button" className={secondary} onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

function SignOutEverywhere() {
  const navigate = useNavigate();
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);

  const go = async () => {
    setBusy(true);
    try {
      await logoutEverywhere();
      navigate("/login", { replace: true });
    } catch (err) {
      toast.error(detail(err, "Could not sign out everywhere. Try again."));
      setBusy(false);
    }
  };

  return (
    <div className={card}>
      <div className="mb-2 flex items-center gap-2">
        <LogOut size={16} className="text-slate-500" aria-hidden="true" />
        <h2 className={heading}>Sign out everywhere</h2>
      </div>
      <p className="mb-4 text-sm text-slate-400">
        Ends every session on every phone and computer, including this one. Use it
        if you signed in somewhere you no longer control, or a sign-in below was
        not you — then change your password.
      </p>
      {sure ? (
        <div className="flex gap-2">
          <button type="button" className={danger} disabled={busy} onClick={go}>
            {busy ? "Signing out…" : "Yes, sign out everywhere"}
          </button>
          <button type="button" className={secondary} onClick={() => setSure(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <button type="button" className={secondary} onClick={() => setSure(true)}>
          Sign out everywhere
        </button>
      )}
    </div>
  );
}

const OUTCOME_STYLE = {
  success: "text-emerald-400",
  failed: "text-red-300",
  mfa_failed: "text-red-300",
  locked: "text-amber-300",
};

function browserOf(ua) {
  if (!ua) return "Unknown";
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iPhone/iPad"
    : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "";
  const br = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /Chrome\//.test(ua) ? "Chrome"
    : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return os ? `${br} on ${os}` : br;
}

function SignInHistory() {
  const isAdmin = getUser()?.role === "tenant_admin";
  const [scope, setScope] = useState("mine");
  const { data, isLoading } = useQuery({
    queryKey: ["login-history", scope],
    queryFn: () =>
      api
        .get("auth/login-history/", { params: scope === "team" ? { scope: "team" } : {} })
        .then((r) => r.data.results),
  });

  return (
    <div className={card}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <History size={16} className="text-slate-500" aria-hidden="true" />
          <h2 className={heading}>Recent sign-ins</h2>
        </div>
        {isAdmin && (
          <div className="flex rounded-lg border border-white/10 p-0.5 text-xs">
            {[["mine", "Mine"], ["team", "My team"]].map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => setScope(k)}
                className={`rounded-md px-3 py-1 ${
                  scope === k ? "bg-white/10 text-white" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="h-24 animate-pulse rounded bg-white/5" />
      ) : !data?.length ? (
        <p className="text-sm text-slate-500">No sign-ins recorded yet.</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {data.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-2 text-sm">
              <span className="min-w-0">
                <span className={`font-medium ${OUTCOME_STYLE[e.outcome] || "text-slate-300"}`}>
                  {e.outcome_label}
                </span>
                {scope === "team" && <span className="text-slate-400"> · {e.username}</span>}
                {e.new_device && (
                  <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-amber-300">
                    New device
                  </span>
                )}
                <span className="block text-xs text-slate-500">
                  {browserOf(e.user_agent)} · {e.ip || "unknown IP"}
                </span>
              </span>
              <time className="text-xs text-slate-500" dateTime={e.at}>
                {new Date(e.at).toLocaleString("en-KE", {
                  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
                })}
              </time>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
