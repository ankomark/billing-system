import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import AuthLayout from "../layouts/AuthLayout";
import {
  login,
  loginWithCode,
  startMfaSetup,
  confirmMfaSetup,
  storeSession,
  getUser,
  isAuthenticated,
} from "../services/auth";
import { homeFor } from "../constants/roles";
import api from "../services/api";

const inputCls =
  "w-full border border-slate-300 rounded-lg px-4 py-2.5 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition";
const buttonCls =
  "w-full bg-blue-600 hover:bg-blue-700 text-white py-2.5 rounded-lg font-semibold text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors mt-2";

const apiError = (err, fallback) => err?.response?.data?.detail || fallback;

/**
 * Signing in, in as many steps as the account needs.
 *
 *   password ─┬─ signed in                     (no two-step on this account)
 *             ├─ code ── signed in             (two-step is on)
 *             └─ setup ── backup codes ── in   (role requires it, not set up yet)
 */
export default function Login() {
  const navigate = useNavigate();
  const [step, setStep] = useState("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [ticket, setTicket] = useState("");
  const [setup, setSetup] = useState(null);       // { qr, secret }
  const [backupCodes, setBackupCodes] = useState(null);
  const [pendingSession, setPendingSession] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) return;
    const user = getUser();
    if (user?.role) navigate(homeFor(user.role), { replace: true });
    else localStorage.clear();
  }, [navigate]);

  const finish = async (data) => {
    storeSession(data);
    // The login response already carries role and tenant, so the profile
    // call is only for the extras (username, operator name).
    const profileRes = await api.get("auth/profile/");
    const user = { ...profileRes.data, role: data.role ?? profileRes.data.role };
    localStorage.setItem("user", JSON.stringify(user));
    // Someone else chose this password — a reset, or a colleague's account
    // just created. Land on the change screen rather than a dashboard.
    navigate(user.must_change_password ? "/change-password" : homeFor(user.role), {
      replace: true,
    });
  };

  const run = async (fn) => {
    setError("");
    setLoading(true);
    try {
      await fn();
    } finally {
      setLoading(false);
    }
  };

  const submitPassword = (e) => {
    e.preventDefault();
    run(async () => {
      let data;
      try {
        data = await login(username, password);
      } catch (err) {
        // Locked out (429) says so and for how long; everything else is the
        // same message, so it does not reveal which usernames exist.
        setError(
          err?.response?.status === 429
            ? apiError(err, "Too many attempts. Please wait a few minutes.")
            : "Invalid username or password. Please try again."
        );
        return;
      }
      if (data.mfa_required) {
        setTicket(data.ticket);
        setStep("code");
      } else if (data.mfa_setup_required) {
        setTicket(data.ticket);
        try {
          setSetup(await startMfaSetup(data.ticket));
          setStep("setup");
        } catch (err) {
          setError(apiError(err, "Could not start two-step setup. Please sign in again."));
        }
      } else {
        await finish(data);
      }
    });
  };

  const submitCode = (e) => {
    e.preventDefault();
    run(async () => {
      try {
        const data = await loginWithCode(ticket, code.trim());
        if (data.backup_codes_left !== undefined && data.backup_codes_left <= 3) {
          // A toast, not the notice box: the page navigates away next.
          toast(
            `You have ${data.backup_codes_left} backup code(s) left. ` +
              "Make new ones under My account → Security.",
            { duration: 8000 }
          );
        }
        await finish(data);
      } catch (err) {
        if (err?.response?.data?.code === "ticket_invalid") {
          backToStart(apiError(err, "That sign-in expired. Please start again."));
        } else {
          setError(apiError(err, "That code did not work."));
        }
      }
    });
  };

  const submitSetup = (e) => {
    e.preventDefault();
    run(async () => {
      try {
        const data = await confirmMfaSetup(code.trim(), ticket);
        setBackupCodes(data.backup_codes);
        setPendingSession(data);
        setStep("backup");
      } catch (err) {
        if (err?.response?.data?.code === "ticket_invalid") {
          backToStart(apiError(err, "That sign-in expired. Please start again."));
        } else {
          setError(apiError(err, "That code did not work."));
        }
      }
    });
  };

  const backToStart = (message = "") => {
    setStep("password");
    setPassword("");
    setCode("");
    setTicket("");
    setSetup(null);
    setError(message);
  };

  return (
    <AuthLayout>
      {step === "password" && (
        <Heading title="Welcome back" subtitle="Sign in to your account to continue" />
      )}
      {step === "code" && (
        <Heading
          title="Two-step verification"
          subtitle="Enter the 6-digit code from your authenticator app, or one of your backup codes."
        />
      )}
      {step === "setup" && (
        <Heading
          title="Set up two-step sign-in"
          subtitle="Your role needs a code from your phone as well as your password. This takes a minute, once."
        />
      )}
      {step === "backup" && (
        <Heading
          title="Save your backup codes"
          subtitle="Each one gets you in once if you lose your phone. They will not be shown again."
        />
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-5 text-sm">
          {error}
        </div>
      )}

      {step === "password" && (
        <form onSubmit={submitPassword} className="space-y-4">
          <Field label="Username">
            <input
              type="text"
              className={inputCls}
              placeholder="Enter your username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              autoFocus
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              className={inputCls}
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          <button type="submit" disabled={loading} className={buttonCls}>
            {loading ? "Signing in…" : "Sign In"}
          </button>
        </form>
      )}

      {step === "code" && (
        <form onSubmit={submitCode} className="space-y-4">
          <CodeInput value={code} onChange={setCode} />
          <button type="submit" disabled={loading || !code.trim()} className={buttonCls}>
            {loading ? "Checking…" : "Verify"}
          </button>
          <BackLink onClick={() => backToStart()} />
        </form>
      )}

      {step === "setup" && setup && (
        <form onSubmit={submitSetup} className="space-y-4">
          <ol className="list-decimal space-y-2 pl-5 text-sm text-slate-600">
            <li>
              Install an authenticator app on your phone — Google Authenticator,
              Microsoft Authenticator or Authy.
            </li>
            <li>In the app, add an account and scan this code.</li>
          </ol>
          <div className="flex justify-center">
            <img
              src={setup.qr}
              alt="QR code for your authenticator app"
              className="h-48 w-48 rounded-lg border border-slate-200"
            />
          </div>
          <p className="text-center text-xs text-slate-500">
            Can't scan? Enter this key instead:
            <br />
            <code className="select-all break-all font-mono text-slate-700">{setup.secret}</code>
          </p>
          <ol start={3} className="list-decimal pl-5 text-sm text-slate-600">
            <li>Type the 6-digit code the app shows.</li>
          </ol>
          <CodeInput value={code} onChange={setCode} />
          <button type="submit" disabled={loading || !code.trim()} className={buttonCls}>
            {loading ? "Checking…" : "Turn on and continue"}
          </button>
          <BackLink onClick={() => backToStart()} />
        </form>
      )}

      {step === "backup" && backupCodes && (
        <BackupCodesStep
          codes={backupCodes}
          onDone={() => run(() => finish(pendingSession))}
          loading={loading}
        />
      )}
    </AuthLayout>
  );
}

function Heading({ title, subtitle }) {
  return (
    <div className="mb-6">
      <h2 className="text-xl font-bold text-slate-800">{title}</h2>
      <p className="text-slate-500 text-sm mt-1">{subtitle}</p>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-slate-700 mb-1.5">{label}</span>
      {children}
    </label>
  );
}

function CodeInput({ value, onChange }) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      inputMode="numeric"
      autoComplete="one-time-code"
      placeholder="123456"
      aria-label="Verification code"
      autoFocus
      className={`${inputCls} text-center text-lg tracking-[0.3em]`}
    />
  );
}

function BackLink({ onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-center text-sm text-slate-500 hover:text-slate-700"
    >
      Use a different account
    </button>
  );
}

export function BackupCodesStep({ codes, onDone, loading, doneLabel = "I've saved them — continue" }) {
  const [saved, setSaved] = useState(false);
  const text = codes.join("\n");
  const download = () => {
    const blob = new Blob(
      [`SmartBill backup codes\nEach works once.\n\n${text}\n`],
      { type: "text/plain" }
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "smartbill-backup-codes.txt";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="space-y-4">
      <ul className="grid grid-cols-2 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-4 font-mono text-sm text-slate-800">
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(text)}
          className="flex-1 rounded-lg border border-slate-300 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Copy
        </button>
        <button
          type="button"
          onClick={download}
          className="flex-1 rounded-lg border border-slate-300 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Download
        </button>
      </div>
      <label className="flex items-start gap-2 text-sm text-slate-600">
        <input
          type="checkbox"
          checked={saved}
          onChange={(e) => setSaved(e.target.checked)}
          className="mt-0.5"
        />
        I have stored these somewhere safe, away from my phone.
      </label>
      <button type="button" disabled={!saved || loading} onClick={onDone} className={buttonCls}>
        {loading ? "Signing in…" : doneLabel}
      </button>
    </div>
  );
}
