import { useEffect, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  fetchHotspotPaymentStatus,
  validateHotspotVoucher,
} from "../../services/hotspot";

const POLL_MS = 3000;
const GIVE_UP_AFTER_MS = 3 * 60 * 1000; // Safaricom prompts expire well inside this

export default function HotspotStatus() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const mac = searchParams.get("mac");
  const reference = searchParams.get("ref");
  const tenantToken = searchParams.get("t");

  const [state, setState] = useState("pending");
  const [voucher, setVoucher] = useState(null);
  const startedAt = useRef(Date.now());

  useEffect(() => {
    if (!mac || !reference) {
      setState("invalid");
      return undefined;
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

          // Redeem it for them.
          //
          // Purchase deliberately does not bind the MAC — an unpaid request
          // must not be able to squat a device that belongs to someone else.
          // But the invoice is paid now and this is the device that paid, so
          // making the customer read a code off one screen and type it into
          // another is a step that exists only because nothing did it for
          // them. If it fails they still have the code below.
          if (data.voucher_code) {
            try {
              await validateHotspotVoucher({
                tenantToken,
                code: data.voucher_code,
                mac,
              });
              const params = new URLSearchParams({ mac, voucher: data.voucher_code });
              if (data.expires_at) params.set("expires", data.expires_at);
              if (tenantToken) params.set("t", tenantToken);
              navigate(`/hotspot/success?${params.toString()}`, { replace: true });
            } catch {
              /* stay here and show them the code to enter by hand */
            }
          }
        } else if (data.status === "not_found") {
          clearInterval(timer);
          setState("invalid");
        }
      } catch {
        // Transient network blips are expected on a captive portal — keep polling.
      }
    }, POLL_MS);

    return () => clearInterval(timer);
  }, [mac, reference, tenantToken, navigate]);

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
          {/* It used to say the code had also been sent by SMS. Messaging
              credentials belong to each operator and are optional, so for an
              operator without them that was simply untrue, and the customer
              stopped writing the code down on the strength of it. */}
          <p className="text-xs text-slate-500 mt-2">
            Write this down — it gets you back online on this phone until it
            expires.
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
