import { useEffect, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { fetchRenewalStatus } from "../../services/customerPortal";

const POLL_MS = 5000;
const GIVE_UP_AFTER_MS = 3 * 60 * 1000; // Safaricom prompts expire well inside this

/**
 * Waiting for a renewal to be paid.
 *
 * This page did not exist. The renew page sent people to
 * /customer/pppoe/status, which was never routed, so the last thing a
 * subscriber saw after paying was the 404 page — their money had gone through
 * and the app told them the page did not exist.
 *
 * There was a PppoeStatus.jsx in the tree that looked like it belonged here.
 * It read a different query parameter than the one being sent, called an
 * endpoint addressed by customer id rather than by invoice, and nothing
 * imported it. It has been removed rather than wired up: it answered a
 * different question than the one being asked at this point in the flow.
 */
export default function PPPoERenewStatus() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const reference = params.get("ref");

  const [state, setState] = useState(reference ? "pending" : "invalid");
  const [paid, setPaid] = useState(null);
  const startedAt = useRef(Date.now());

  useEffect(() => {
    if (!reference) return undefined;

    let timer;
    let delay = POLL_MS;

    const tick = async () => {
      if (Date.now() - startedAt.current > GIVE_UP_AFTER_MS) {
        setState("timeout");
        return;
      }
      try {
        const data = await fetchRenewalStatus(reference);
        delay = POLL_MS;
        if (data.status === "paid") {
          setPaid(data);
          setState("paid");
          // Give them a moment to read it, then back to the portal, which is
          // where the new expiry date actually lives.
          setTimeout(() => navigate("/customer/pppoe", { replace: true }), 3500);
          return;
        }
        if (data.status === "not_found") {
          setState("invalid");
          return;
        }
      } catch (e) {
        // Slowing down is the only correct response to being told we are
        // asking too often; everything else here is a transient blip.
        if (e.response?.status === 429) delay = Math.min(delay * 2, 20000);
      }
      timer = setTimeout(tick, delay);
    };

    timer = setTimeout(tick, delay);
    return () => clearTimeout(timer);
  }, [reference, navigate]);

  const views = {
    pending: {
      icon: "📱",
      tone: "text-slate-800",
      title: "Waiting for payment…",
      body: "Approve the M-Pesa prompt on your phone.",
      extra: (
        <p className="mt-3 animate-pulse text-sm font-semibold text-blue-600">
          Checking…
        </p>
      ),
    },
    paid: {
      icon: "✅",
      tone: "text-emerald-600",
      title: "Renewed",
      body: paid?.expires_at
        ? `Your subscription now runs until ${new Date(paid.expires_at).toLocaleString("en-KE", {
            day: "numeric", month: "short", year: "numeric",
            hour: "numeric", minute: "2-digit",
          })}.`
        : "Your subscription has been renewed.",
      extra: <p className="mt-3 text-xs text-slate-500">Taking you back…</p>,
    },
    timeout: {
      icon: "⌛",
      tone: "text-amber-600",
      title: "No payment received",
      body: "The prompt may have expired. You can safely try again — you have not been charged.",
    },
    invalid: {
      icon: "⚠️",
      tone: "text-red-600",
      title: "We couldn't find that renewal",
      body: "Start again from your account page.",
    },
  };

  const view = views[state] || views.pending;

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 p-6">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 text-center shadow-lg">
        <div className="mb-4 text-5xl">{view.icon}</div>
        <h2 className={`mb-2 text-xl font-bold ${view.tone}`}>{view.title}</h2>
        <p className="text-sm text-slate-600">{view.body}</p>
        {view.extra}

        {state !== "paid" && (
          <button
            onClick={() => navigate("/customer/pppoe", { replace: true })}
            className="mt-6 w-full rounded-xl border border-slate-300 py-2.5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50"
          >
            Back to my account
          </button>
        )}
      </div>
    </div>
  );
}
