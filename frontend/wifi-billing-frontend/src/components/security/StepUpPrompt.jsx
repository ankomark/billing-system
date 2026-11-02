import { useEffect, useRef, useState } from "react";
import { ShieldCheck } from "lucide-react";
import api from "../../services/api";
import { onStepUpRequest } from "../../lib/stepUp";

/**
 * The "enter your code to continue" dialog for sensitive actions.
 *
 * Mounted once, at the root. It does nothing until the API client meets a
 * request the server refused for want of a fresh code (see lib/stepUp.js);
 * then it asks, confirms the code with the server, and lets the original
 * request go through. Cancelling leaves the action undone.
 */
export default function StepUpPrompt() {
  const [request, setRequest] = useState(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => onStepUpRequest((r) => {
    setCode("");
    setError("");
    setRequest(r);
  }), []);

  useEffect(() => {
    if (request) inputRef.current?.focus();
  }, [request]);

  if (!request) return null;

  const cancel = () => {
    request.reject(new Error("cancelled"));
    setRequest(null);
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post("auth/mfa/verify/", { code: code.trim() });
      request.resolve();
      setRequest(null);
    } catch (err) {
      setError(err.response?.data?.detail || "That code did not work. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="stepup-title"
      onKeyDown={(e) => e.key === "Escape" && cancel()}
    >
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-white/10 bg-slate-900 p-6 shadow-2xl"
      >
        <div className="mb-3 flex items-center gap-2">
          <ShieldCheck size={18} className="text-blue-400" aria-hidden="true" />
          <h2 id="stepup-title" className="text-base font-semibold text-white">
            Confirm it's you
          </h2>
        </div>
        <p className="mb-4 text-sm text-slate-400">
          This change needs a code from your authenticator app. One code covers
          the next ten minutes.
        </p>
        <input
          ref={inputRef}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          aria-label="Authenticator code"
          className="w-full rounded-lg border border-white/15 bg-slate-950 px-4 py-2.5 text-center text-lg tracking-[0.3em] text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {error && <p className="mt-2 text-sm text-red-300">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={cancel}
            className="rounded-lg border border-white/15 px-4 py-2 text-sm font-semibold text-slate-300 hover:bg-white/5"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || !code.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {busy ? "Checking…" : "Confirm"}
          </button>
        </div>
      </form>
    </div>
  );
}
