import { useState } from "react";
import { KeyRound, X } from "lucide-react";
import toast from "react-hot-toast";
import { createCustomerLogin } from "../../services/customers";

/**
 * Give a PPPoE subscriber a login, or reset the one they have.
 *
 * The renewal portal has existed all along and no subscriber could reach it,
 * because nothing ever created them an account — so every renewal was the
 * operator taking money by hand.
 *
 * Two screens on purpose. The first confirms, because resetting signs the
 * holder out of a session they may be using. The second shows the password,
 * and is the only place it will ever be readable: it is hashed the moment it
 * is set. So it does not close on a stray click, and it says plainly whether
 * the message actually went.
 */
export default function CustomerLoginModal({ open, customer, onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);

  if (!open) return null;

  const existing = !!customer?.has_login;

  const close = () => {
    setIssued(null);
    setBusy(false);
    onClose();
  };

  const submit = async () => {
    setBusy(true);
    try {
      const res = await createCustomerLogin(customer.id);
      setIssued(res);
      onDone?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Couldn't do that.");
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
          <div className="flex items-center gap-2">
            <KeyRound size={18} className="text-sky-300" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-white">
              {issued
                ? "Their sign-in details"
                : existing
                ? "Reset portal password"
                : "Create a portal login"}
            </h2>
          </div>
          <button
            onClick={close}
            aria-label="Close"
            className="rounded-lg p-1 text-slate-400 transition-colors hover:bg-white/5 hover:text-white"
          >
            <X size={18} />
          </button>
        </div>

        {!issued ? (
          <div className="space-y-4 px-5 py-5">
            <p className="text-sm text-slate-300">
              {existing ? (
                <>
                  This gives {customer?.full_name} a new password and signs them
                  out of the portal everywhere they are currently signed in.
                </>
              ) : (
                <>
                  {customer?.full_name} will be able to sign in and renew their
                  own package, instead of paying you by hand each month.
                </>
              )}
            </p>
            <div className="rounded-lg border border-white/10 bg-slate-950/50 px-4 py-3">
              <p className="text-xs text-slate-500">They sign in with</p>
              <p className="mt-0.5 font-mono text-sm text-slate-200">
                {customer?.pppoe_username || "—"}
              </p>
              <p className="mt-2 text-xs text-slate-500">
                Their PPPoE username — already set on their router, and already
                sent to them when the line was created.
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={close}
                className="rounded-lg px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-white/5"
              >
                Cancel
              </button>
              <button
                onClick={submit}
                disabled={busy || !customer?.pppoe_username}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-sky-500 disabled:opacity-50"
              >
                {busy
                  ? "Working…"
                  : existing
                  ? "Reset password"
                  : "Create login"}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4 px-5 py-5">
            {/* Written down before it is gone. The password exists nowhere else
                — it was hashed the moment it was set — so this panel is the
                only chance anyone has to read it. */}
            <div className="rounded-lg border border-sky-500/30 bg-sky-500/5 px-4 py-3">
              <p className="text-xs text-slate-400">Username</p>
              <p className="select-all font-mono text-base text-white">
                {issued.username}
              </p>
              <p className="mt-3 text-xs text-slate-400">Password</p>
              <p className="select-all font-mono text-base text-white">
                {issued.password}
              </p>
            </div>

            <p className="text-xs text-amber-300">
              Write this down now. It is not stored anywhere and cannot be shown
              again — you would have to reset it.
            </p>

            <p className="text-xs text-slate-400">
              {issued.sms_sent || issued.whatsapp_sent ? (
                <>
                  Also sent to them by{" "}
                  {[
                    issued.sms_sent && "SMS",
                    issued.whatsapp_sent && "WhatsApp",
                  ]
                    .filter(Boolean)
                    .join(" and ")}
                  .
                </>
              ) : (
                <span className="text-amber-300">
                  The message could not be sent — read these out to them. Check
                  your SMS credit in System Settings.
                </span>
              )}
            </p>

            <p className="text-xs text-slate-500">
              They will be asked to choose their own password when they first
              sign in.
            </p>

            <div className="flex justify-end pt-1">
              <button
                onClick={close}
                className="rounded-lg bg-slate-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-slate-600"
              >
                Done
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
