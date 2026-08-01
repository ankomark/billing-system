import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Confirmation that a device is online.
 *
 * Reached two ways: straight after paying, and — since the portal started
 * checking devices before selling to them — by any device that came back with
 * time still on it.
 *
 * The voucher code is shown here because it was previously shown nowhere. It
 * went out in one SMS at purchase, and an operator with no BlessedTexts
 * credentials sends no SMS at all, so for those customers the code they had
 * paid for existed only in the database.
 */
export default function HotspotSuccess() {
  const [params] = useSearchParams();

  const mac = params.get("mac") || "Unknown";
  const expires = params.get("expires");
  const voucher = params.get("voucher");
  const packageName = params.get("package");

  const [countdown, setCountdown] = useState(8);
  const [copied, setCopied] = useState(false);
  const [paused, setPaused] = useState(false);

  const reconnect = () => {
    window.location.href = `http://login.hotspot/?username=${mac}`;
  };

  // Auto redirect after countdown.
  //
  // Pauses while the code is on screen and uncopied. It used to run at six
  // seconds flat, which is not long enough to copy a code down, and the
  // redirect leaves the page — so the one moment the customer could see it
  // was also the one moment they were being hurried away from it.
  useEffect(() => {
    if (paused) return undefined;
    const timer = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          reconnect();
          return 0;
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, mac]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(voucher);
      setCopied(true);
      setPaused(false);
    } catch {
      // Clipboard is unavailable over plain http on some phones, which is
      // exactly how a captive portal is served. The code is selectable.
      setPaused(true);
    }
  };

  const expiresText = expires
    ? new Date(expires).toLocaleString("en-KE", {
        day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
      })
    : "N/A";

  return (
    <div className="min-h-screen flex items-center justify-center bg-emerald-50 p-6">
      <div className="bg-white p-6 rounded-xl shadow text-center max-w-sm w-full">
        <h2 className="text-2xl font-bold text-emerald-600 mb-2">
          You're connected
        </h2>
        <p className="text-slate-600 mb-4 text-sm">
          Your device has internet access.
        </p>

        {voucher && (
          <div className="mb-4 rounded-xl border-2 border-emerald-500/30 bg-emerald-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
              Your access code
            </p>
            <p className="my-2 select-all font-mono text-2xl font-bold tracking-wider text-slate-900">
              {voucher}
            </p>
            <button
              onClick={copy}
              className="w-full rounded-lg bg-emerald-600 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-700"
            >
              {copied ? "Copied" : "Copy code"}
            </button>
            <p className="mt-2 text-xs text-emerald-800">
              Write this down. It gets you back online on this phone until it
              expires, without paying again.
            </p>
          </div>
        )}

        <div className="mb-4 rounded-lg bg-slate-100 p-3 text-left text-sm">
          {packageName && (
            <p className="flex justify-between gap-3">
              <span className="text-slate-500">Package</span>
              <span className="font-medium text-slate-800">{packageName}</span>
            </p>
          )}
          <p className="flex justify-between gap-3">
            <span className="text-slate-500">Expires</span>
            <span className="font-medium text-slate-800">{expiresText}</span>
          </p>
          <p className="flex justify-between gap-3">
            <span className="text-slate-500">Device</span>
            <span className="font-mono text-xs text-slate-600">{mac}</span>
          </p>
        </div>

        <button
          onClick={reconnect}
          className="w-full rounded-lg bg-blue-600 py-2.5 font-semibold text-white transition-colors hover:bg-blue-700"
        >
          Start browsing
        </button>

        {paused ? (
          <button
            onClick={reconnect}
            className="mt-3 text-xs text-slate-500 underline"
          >
            Done with the code — continue
          </button>
        ) : (
          <p className="mt-3 text-xs text-slate-500">
            Continuing automatically in <b>{countdown}</b> seconds…
          </p>
        )}

        <p className="mt-2 text-xs text-slate-400">
          If you disconnect later, just reconnect to the WiFi — no extra
          payment until this expires.
        </p>
      </div>
    </div>
  );
}
