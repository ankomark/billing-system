import { useState } from "react";
import { Check, Copy, KeyRound, X } from "lucide-react";
import toast from "react-hot-toast";
import { resetOperatorPassword } from "../../services/platform";

/**
 * Set a temporary password for an operator who has lost theirs.
 *
 * There is no email backend and the SMS credentials belong to each operator
 * rather than the platform, so a self-service reset link is not available. The
 * owner reads the new password out to them, which is how support already works.
 *
 * The password is shown once. It is not stored in readable form and not
 * written to any log, so there is nowhere to look it up afterwards — the copy
 * button and the warning are the whole mitigation.
 */
export default function ResetPasswordModal({ open, operator, onClose }) {
  const [reason, setReason] = useState("");
  const [username, setUsername] = useState("");
  const [result, setResult] = useState(null);
  const [choices, setChoices] = useState(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!open) return null;

  const close = () => {
    setReason("");
    setUsername("");
    setResult(null);
    setChoices(null);
    setCopied(false);
    onClose();
  };

  const submit = async () => {
    if (!reason.trim()) {
      toast.error("Say why — it goes in the audit log.");
      return;
    }
    setBusy(true);
    try {
      const data = await resetOperatorPassword(operator.id, {
        username: username || undefined,
        reason: reason.trim(),
      });
      setResult(data);
      setChoices(null);
    } catch (e) {
      const data = e.response?.data;
      if (data?.usernames) {
        // Several admins — the backend refuses to guess which one.
        setChoices(data.usernames);
        toast.error(data.detail);
      } else {
        toast.error(data?.detail || "Couldn't reset the password");
      }
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(result.temporary_password);
      setCopied(true);
    } catch {
      toast.error("Couldn't copy — select it manually.");
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Reset operator password"
    >
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-slate-900 shadow-2xl">
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-white/10">
          <div>
            <h2 className="font-bold text-white flex items-center gap-2">
              <KeyRound size={16} aria-hidden="true" />
              Reset password
            </h2>
            <p className="text-slate-400 text-sm mt-0.5">{operator?.name}</p>
          </div>
          <button
            onClick={close}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {result ? (
          <div className="px-5 py-4 space-y-4">
            <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              This is shown once. It is not stored anywhere readable — if you
              lose it you will have to reset again.
            </div>

            <div>
              <p className="text-xs text-slate-400 mb-1">Username</p>
              <p className="font-mono text-sm text-slate-100">{result.username}</p>
            </div>

            <div>
              <p className="text-xs text-slate-400 mb-1">Temporary password</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded-lg border border-white/10 bg-slate-950 px-3 py-2 font-mono text-sm text-teal-300 select-all">
                  {result.temporary_password}
                </code>
                <button
                  onClick={copy}
                  className="rounded-lg border border-white/15 px-3 py-2 text-slate-300 hover:bg-white/5 transition-colors"
                  aria-label="Copy password"
                >
                  {copied ? <Check size={15} className="text-emerald-400" /> : <Copy size={15} />}
                </button>
              </div>
            </div>

            <p className="text-xs text-slate-500">
              They are signed out of every session, and will be made to choose
              their own password the first time they sign in.
            </p>

            <button
              onClick={close}
              className="w-full bg-teal-500 hover:bg-teal-400 text-slate-950 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
            >
              Done
            </button>
          </div>
        ) : (
          <div className="px-5 py-4 space-y-4">
            <p className="text-sm text-slate-400">
              Sets a temporary password and signs out every session this account
              has. Read it out to them directly.
            </p>

            {choices && (
              <label className="block">
                <span className="text-sm font-medium text-slate-300">
                  Which account?
                </span>
                <select
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
                >
                  <option value="">Choose…</option>
                  {choices.map((u) => (
                    <option key={u} value={u}>{u}</option>
                  ))}
                </select>
              </label>
            )}

            <label className="block">
              <span className="text-sm font-medium text-slate-300">
                Why are you resetting this?
              </span>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Called in, identity verified against contact number"
                className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-teal-500"
              />
            </label>

            <div className="flex items-center gap-3">
              <button
                onClick={submit}
                disabled={busy}
                className="bg-red-500 hover:bg-red-400 disabled:opacity-60 text-slate-950 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
              >
                {busy ? "Resetting…" : "Reset password"}
              </button>
              <button
                onClick={close}
                className="text-slate-400 hover:text-white text-sm font-medium transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
