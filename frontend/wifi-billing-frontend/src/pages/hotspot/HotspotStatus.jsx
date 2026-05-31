import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../../services/api";

export default function HotspotStatus() {
  const [searchParams] = useSearchParams();
  const mac            = searchParams.get("mac");
  const subscriptionId = searchParams.get("subscription");

  const [status, setStatus]     = useState("pending");
  const [expiresAt, setExpiresAt] = useState(null);

  useEffect(() => {
    if (!mac || !subscriptionId) {
      setStatus("invalid_params");
      return;
    }

    const interval = setInterval(async () => {
      try {
        const res  = await api.get(`subscriptions/${subscriptionId}/`);
        const data = res.data;

        if (data.invoice?.payment_status === "paid") {
          setExpiresAt(data.expiry_date);
          setStatus("active");
          clearInterval(interval);
          setTimeout(() => {
            window.location.href = `/hotspot/success?mac=${mac}&expires=${data.expiry_date}`;
          }, 2000);
        } else {
          setStatus("pending");
        }
      } catch {
        setStatus("error");
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [mac, subscriptionId]);

  const content = {
    invalid_params: {
      icon: "⚠️",
      title: "Invalid Request",
      titleClass: "text-red-600",
      body: "Missing MAC or subscription ID. Please reconnect through the hotspot portal.",
    },
    pending: {
      icon: "📱",
      title: "Waiting for Payment…",
      titleClass: "text-slate-800",
      body: "Please approve the M-Pesa STK push prompt on your phone.",
      extra: <p className="text-blue-600 font-semibold animate-pulse mt-3">Checking payment status…</p>,
    },
    active: {
      icon: "✅",
      title: "Payment Confirmed!",
      titleClass: "text-emerald-600",
      body: "Your device is being connected. Please wait…",
      extra: expiresAt && <p className="text-slate-600 mt-2 text-sm">Expires: {new Date(expiresAt).toLocaleString()}</p>,
    },
    error: {
      icon: "❌",
      title: "Connection Error",
      titleClass: "text-red-600",
      body: "Cannot reach the server. Please check your connection.",
    },
  }[status] || {};

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100 p-6">
      <div className="bg-white rounded-2xl shadow-lg p-8 text-center w-full max-w-sm">
        <div className="text-5xl mb-4">{content.icon}</div>
        <h2 className={`text-xl font-bold mb-2 ${content.titleClass}`}>{content.title}</h2>
        <p className="text-slate-600 text-sm">{content.body}</p>
        {content.extra}
      </div>
    </div>
  );
}
