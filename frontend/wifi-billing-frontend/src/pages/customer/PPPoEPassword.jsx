import { useState } from "react";
import {
  changePPPoEPassword,
  fetchPPPoEPasswordSuggestion,
} from "../../services/pppoe";

/**
 * Changing the PPPoE password, from the subscriber's side.
 *
 * This is what a subscriber can actually do about somebody else using their
 * line. Their devices cannot be managed from the operator's router — every
 * phone and laptop behind their own router is NATted into one address and one
 * session before it reaches us, so there is nothing to list and nothing to
 * block individually. Taking the credentials away is the whole lever.
 *
 * Three states, deliberately: asking, confirming, and done. The confirm step
 * is not ceremony — this knocks them offline until they retype the password
 * into their own router, and somebody who taps it not knowing that has just
 * broken their own internet and does not know why.
 */
export default function PPPoEPassword() {
  const [step, setStep] = useState("idle"); // idle | confirm | done
  const [password, setPassword] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const open = async () => {
    setError("");
    setBusy(true);
    try {
      const { suggested_password } = await fetchPPPoEPasswordSuggestion();
      setPassword(suggested_password);
      setStep("confirm");
    } catch {
      setError("Couldn't start just now — please try again.");
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    setError("");
    setBusy(true);
    try {
      setResult(await changePPPoEPassword(password));
      setStep("done");
    } catch (e) {
      // The server's message is written to be read by a subscriber — the
      // reason a password was refused, or that no router could be reached and
      // nothing changed. Showing our own generic text instead would hide the
      // one useful sentence.
      setError(
        e?.response?.data?.detail ||
          "Couldn't change it just now — please try again."
      );
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(result.password);
      setCopied(true);
    } catch {
      // Clipboard is blocked on plain http, which is exactly where a captive
      // portal lives. The password is on screen either way, so this is a
      // convenience and never the only way to get it.
    }
  };

  if (step === "done" && result) {
    return (
      <div className="mt-6 rounded border border-emerald-300 bg-emerald-50 p-4 text-sm">
        <h3 className="font-bold mb-2">Your new password</h3>

        <div className="flex items-center gap-2">
          <code className="flex-1 rounded bg-white px-3 py-2 font-mono text-base tracking-wide break-all">
            {result.password}
          </code>
          <button
            onClick={copy}
            className="rounded bg-emerald-600 px-3 py-2 text-white"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>

        <p className="mt-3">
          <b>You are offline until you enter it.</b> Open your router's settings,
          find the PPPoE or internet section, and replace the old password with
          this one. Your username stays{" "}
          <code className="font-mono">{result.username}</code>.
        </p>

        {result.routers_unreachable?.length > 0 && (
          // Said plainly rather than buried: on that router the old password
          // still works, so whoever they are locking out is not locked out yet.
          <p className="mt-3 rounded bg-amber-100 p-2 text-amber-900">
            One of our routers couldn't be reached, so the old password may
            still work there for a short while. Tell us if someone is still
            using your line tomorrow.
          </p>
        )}

        <p className="mt-3 text-slate-600">
          Write it down somewhere safe — we can't show it again after you leave
          this page.
        </p>
      </div>
    );
  }

  if (step === "confirm") {
    return (
      <div className="mt-6 rounded border border-amber-300 bg-amber-50 p-4 text-sm">
        <h3 className="font-bold mb-2">Change your internet password</h3>

        <p className="mb-3">
          This stops anyone who knows your old password from using your line.{" "}
          <b>It also disconnects you</b> until you enter the new password into
          your own router — have it to hand before you continue.
        </p>

        <label className="block mb-1 font-medium" htmlFor="pppoe-new-password">
          New password
        </label>
        <input
          id="pppoe-new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded border px-3 py-2 font-mono"
          autoComplete="off"
        />
        <p className="mt-1 text-xs text-slate-600">
          Letters and numbers only, at least 8. We've suggested one — you can
          keep it or type your own.
        </p>

        {error && <p className="mt-3 text-red-600">{error}</p>}

        <div className="mt-4 flex gap-2">
          <button
            onClick={submit}
            disabled={busy}
            className="rounded bg-amber-600 px-4 py-2 text-white disabled:opacity-60"
          >
            {busy ? "Changing…" : "Change it now"}
          </button>
          <button
            onClick={() => {
              setStep("idle");
              setError("");
            }}
            disabled={busy}
            className="rounded border px-4 py-2"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-6">
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <button
        onClick={open}
        disabled={busy}
        className="w-full rounded border border-slate-300 py-2 text-sm font-medium disabled:opacity-60"
      >
        {busy ? "Please wait…" : "Change my internet password"}
      </button>
      <p className="mt-1 text-center text-xs text-slate-500">
        Use this if someone else is on your line.
      </p>
    </div>
  );
}
