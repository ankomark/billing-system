import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fetchHotspotPaymentStatus } from "../../services/hotspot";

const POLL_MS = 3000;
const GIVE_UP_AFTER_MS = 3 * 60 * 1000; // Safaricom prompts expire well inside this

export default function HotspotStatus() {
  const [searchParams] = useSearchParams();
  const mac = searchParams.get("mac");
  const reference = searchParams.get("ref");
  const tenantToken = searchParams.get("t");

  const [state, setState] = useState("pending");
  const [voucher, setVoucher] = useState(null);
  const startedAt = useRef(Date.now());

  useEffect(() => {
    if (!mac || !reference) {
      setState("invalid");
      return;
    }

    const timer = setInterval(async () => {
      if (Date.now() - startedAt.current > GIVE_UP_AFTER_MS) {
        clearInterval(timer);
        setState("timeout");
        return;
      }

      try {
        // Polls a public endpoint. This previously fetched the admin-only
        // subscriptions endpoint, which always returned 403 for a walk-up
        // customer, so the page never left "waiting".
        const data = await fetchHotspotPaymentStatus({ tenantToken, reference });

        if (data.status === "paid") {
          clearInterval(timer);
          setVoucher(data.voucher_code);
          setState("paid");
        } else if (data.status === "not_found") {
          clearInterval(timer);
          setState("invalid");
        }
      } catch {
        // Transient network blips are expected on a captive portal — keep polling.
      }
    }, POLL_MS);

    return () => clearInterval(timer);
  }, [mac, reference, tenantToken]);

  const views = {
    pending: {
      icon: "📱",
      title: "Waiting for payment…",
      tone: "text-slate-800",
      body: "Approve the M-Pesa prompt on your phone.",
      extra: (
        <p className="text-blue-600 font-semibold animate-pulse mt-3 text-sm">
          Checking…
        </p>
      ),
    },
    paid: {
      icon: "✅",
      title: "Payment received",
      tone: "text-emerald-600",
      body: voucher
        ? "Enter this code on the WiFi login page to connect:"
        : "You're all set. Reconnect to the WiFi to get online.",
      extra: voucher && (
        <div className="mt-4">
          <code className="block bg-slate-100 text-slate-800 font-mono text-lg tracking-wider py-3 rounded-lg">
            {voucher}
          </code>
          <p className="text-xs text-slate-500 mt-2">
            We've also sent this to you by SMS.
          </p>
        </div>
      ),
    },
    timeout: {
      icon: "⌛",
      title: "No payment received",
      tone: "text-amber-600",
      body: "The prompt may have expired. You can safely try again — you have not been charged.",
    },
    invalid: {
      icon: "⚠️",
      title: "Invalid request",
      tone: "text-red-600",
      body: "Please reconnect through the WiFi login page and start again.",
    },
  };

  const view = views[state] || views.pending;

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100 p-6">
      <div className="bg-white rounded-2xl shadow-lg p-8 text-center w-full max-w-sm">
        <div className="text-5xl mb-4">{view.icon}</div>
        <h2 className={`text-xl font-bold mb-2 ${view.tone}`}>{view.title}</h2>
        <p className="text-slate-600 text-sm">{view.body}</p>
        {view.extra}
      </div>
    </div>
  );
}
