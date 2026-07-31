import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { KeyRound } from "lucide-react";
import toast from "react-hot-toast";
import AuthLayout from "../layouts/AuthLayout";
import { changePassword } from "../services/account";
import { getUser } from "../services/auth";
import { homeFor } from "../constants/roles";

/**
 * Set your own password.
 *
 * Reached two ways: chosen from the account page, or forced — an account whose
 * password was set by someone else lands here and cannot go anywhere until it
 * has its own. `forced` only changes the copy; the guard that keeps someone
 * here lives in ProtectedRoute.
 */
export default function ChangePassword({ forced = false }) {
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErrors({});

    if (next !== confirm) {
      setErrors({ confirm: "These do not match." });
      return;
    }

    setBusy(true);
    try {
      await changePassword({ current_password: current, new_password: next });

      // The account no longer needs forcing; the cached copy would otherwise
      // send us straight back here.
      const user = getUser();
      if (user) {
        localStorage.setItem(
          "user",
          JSON.stringify({ ...user, must_change_password: false })
        );
      }

      toast.success("Password changed. Other sessions have been signed out.");
      navigate(homeFor(user?.role), { replace: true });
    } catch (err) {
      const data = err.response?.data;
      if (data && typeof data === "object" && !data.detail) {
        setErrors(data);
      } else {
        toast.error(data?.detail || "Couldn't change the password");
      }
    } finally {
      setBusy(false);
    }
  };

  const fieldError = (k) =>
    Array.isArray(errors[k]) ? errors[k].join(" ") : errors[k];

  return (
    <AuthLayout>
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-2">
          <KeyRound size={18} className="text-teal-600" aria-hidden="true" />
          <h2 className="text-xl font-bold text-slate-800">
            {forced ? "Set your password" : "Change your password"}
          </h2>
        </div>
        <p className="text-slate-500 text-sm">
          {forced
            ? "This password was set for you by someone else. Choose your own before continuing."
            : "Changing your password signs out every other session."}
        </p>
      </div>

      <form onSubmit={submit} className="space-y-4">
        <Field label="Current password" error={fieldError("current_password")}>
          <input
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
            required
            className={input(fieldError("current_password"))}
          />
        </Field>

        <Field
          label="New password"
          error={fieldError("new_password")}
          hint="At least 8 characters, and not something guessable."
        >
          <input
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            required
            className={input(fieldError("new_password"))}
          />
        </Field>

        <Field label="Confirm new password" error={fieldError("confirm")}>
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
            className={input(fieldError("confirm"))}
          />
        </Field>

        <button
          type="submit"
          disabled={busy}
          className="w-full bg-teal-600 hover:bg-teal-700 disabled:opacity-60 text-white rounded-lg py-2.5 text-sm font-semibold transition-colors"
        >
          {busy ? "Saving…" : "Change password"}
        </button>
      </form>
    </AuthLayout>
  );
}

const input = (hasError) =>
  `w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
    hasError ? "border-red-300 focus:ring-red-400" : "border-slate-300 focus:ring-teal-500"
  }`;

function Field({ label, hint, error, children }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <div className="mt-1">{children}</div>
      {error && <span className="text-xs text-red-600 mt-1 block">{error}</span>}
      {!error && hint && (
        <span className="text-xs text-slate-400 mt-1 block">{hint}</span>
      )}
    </label>
  );
}
