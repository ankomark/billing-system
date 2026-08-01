import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  fetchHotspotPackages,
  fetchHotspotDeviceStatus,
  reconnectHotspotDevice,
  validateHotspotVoucher,
} from "../../services/hotspot";

/**
 * The captive portal's landing page.
 *
 * It used to go straight to the package list. That meant every device that
 * dropped off and came back — a power cut, a phone that slept, someone
 * carrying it to a friend's house on the same router — was asked to pay again
 * for time it had already bought. The endpoints to check and restore access
 * both existed the whole time and nothing called them.
 *
 * So: ask about this device first, and only sell to it if it has nothing.
 */
export default function HotspotPackages() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(true);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // MikroTik appends mac/ip; `t` identifies whose portal this is.
  const mac = searchParams.get("mac");
  const tenantToken = searchParams.get("t");

  // Does this device already have time left?
  useEffect(() => {
    let cancelled = false;
    if (!mac) {
      setChecking(false);
      return undefined;
    }

    (async () => {
      try {
        const status = await fetchHotspotDeviceStatus({ tenantToken, mac });
        if (cancelled) return;

        if (status?.status === "active") {
          // Tell the router to let them back on. If this fails the customer
          // still has valid time, so send them to the success page anyway —
          // it offers the manual reconnect, which is the same action.
          try {
            await reconnectHotspotDevice({ tenantToken, mac });
          } catch {
            /* handled by the success page's reconnect button */
          }
          if (cancelled) return;

          const params = new URLSearchParams({ mac });
          if (status.expires_at) params.set("expires", status.expires_at);
          if (status.voucher_code) params.set("voucher", status.voucher_code);
          if (status.package) params.set("package", status.package);
          if (tenantToken) params.set("t", tenantToken);
          navigate(`/hotspot/success?${params.toString()}`, { replace: true });
          return;
        }
      } catch {
        // Never block the sale on this. A device we cannot ask about is
        // treated as a device with nothing, which is the old behaviour.
      }
      if (!cancelled) setChecking(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [mac, tenantToken, navigate]);

  useEffect(() => {
    if (!mac || checking) {
      return;
    }
    fetchHotspotPackages(tenantToken)
      .then(setData)
      .catch((err) => {
        setError(
          err.response?.status === 404
            ? "We couldn't identify your internet provider. Please reconnect through the WiFi login page."
            : "Couldn't load packages. Please try again."
        );
      })
      .finally(() => setLoading(false));
  }, [mac, tenantToken, checking]);

  if (!mac) {
    return (
      <Centered>
        <h2 className="text-xl font-bold text-red-600 mb-2">Device not recognised</h2>
        <p className="text-slate-600">Please reconnect through the WiFi login page.</p>
      </Centered>
    );
  }

  if (checking) {
    return (
      <Centered>
        <p className="text-slate-600">Checking your device…</p>
        <p className="text-xs text-slate-400 mt-1">
          If you already have time left, we'll put you straight back online.
        </p>
      </Centered>
    );
  }

  if (loading) {
    return <Centered><p className="text-slate-600">Loading packages…</p></Centered>;
  }

  if (error) {
    return (
      <Centered>
        <h2 className="text-xl font-bold text-red-600 mb-2">Something went wrong</h2>
        <p className="text-slate-600">{error}</p>
      </Centered>
    );
  }

  const packages = data?.results ?? [];

  return (
    <div className="min-h-screen bg-slate-100 p-4">
      <div className="max-w-md mx-auto">
        <h1 className="text-xl font-bold text-center text-slate-800">
          Choose a package
        </h1>
        {data?.provider && (
          <p className="text-center text-sm text-slate-500 mt-1">{data.provider}</p>
        )}
        <p className="text-center text-xs text-slate-400 mt-2 mb-5 font-mono">{mac}</p>

        {packages.length === 0 ? (
          <div className="bg-white rounded-xl p-6 text-center text-slate-500 text-sm">
            No packages are available right now. Please ask at the counter.
          </div>
        ) : (
          <div className="space-y-3">
            {packages.map((pkg) => (
              <button
                key={pkg.id}
                onClick={() =>
                  navigate(
                    `/hotspot/pay?package=${pkg.id}&mac=${encodeURIComponent(mac)}` +
                      (tenantToken ? `&t=${encodeURIComponent(tenantToken)}` : "")
                  )
                }
                className="w-full text-left bg-white rounded-xl p-4 shadow-sm hover:shadow-md transition-shadow"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-semibold text-slate-800">{pkg.name}</p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      {pkg.download_speed}/{pkg.upload_speed} Mbps · {pkg.duration}
                    </p>
                    {pkg.monthly_data_cap_gb > 0 && (
                      <p className="text-xs text-slate-400 mt-0.5">
                        {pkg.monthly_data_cap_gb} GB included
                      </p>
                    )}
                  </div>
                  <p className="font-bold text-blue-600 text-lg whitespace-nowrap">
                    KES {Number(pkg.price).toLocaleString()}
                  </p>
                </div>
              </button>
            ))}
          </div>
        )}

        <RedeemPanel mac={mac} tenantToken={tenantToken} navigate={navigate} />
      </div>
    </div>
  );
}

/**
 * "I've already paid."
 *
 * Two things land people here: they paid, the STK confirmation arrived, and
 * the portal moved on or the phone slept; or they are back on a device whose
 * binding was cleared. Either way they are holding an M-Pesa message and had
 * no way to use it — the code they were told to keep did nothing anywhere in
 * the interface.
 *
 * The whole message is accepted, because that is what a paste produces.
 */
function RedeemPanel({ mac, tenantToken, navigate }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setProblem("");
    try {
      const res = await validateHotspotVoucher({ tenantToken, code: text.trim(), mac });
      const params = new URLSearchParams({ mac });
      if (res?.expires_at) params.set("expires", res.expires_at);
      if (tenantToken) params.set("t", tenantToken);
      navigate(`/hotspot/success?${params.toString()}`, { replace: true });
    } catch (err) {
      setProblem(
        err.response?.data?.detail ||
          "We couldn't match that. Check you pasted the whole message."
      );
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-5 w-full rounded-xl border border-slate-300 bg-white py-3 text-sm font-semibold text-slate-700"
      >
        Already paid? Use your code
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="mt-5 rounded-xl bg-white p-4 shadow-sm">
      <label className="block text-sm font-semibold text-slate-800">
        Paste your M-Pesa message
      </label>
      <p className="mt-0.5 mb-2 text-xs text-slate-500">
        Or type your access code. The whole message is fine — we'll find the
        code in it.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        autoFocus
        placeholder="TGX11AA001 Confirmed. Ksh50.00 sent to…"
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      {problem && <p className="mt-2 text-sm text-red-600">{problem}</p>}
      <div className="mt-3 flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "Checking…" : "Connect me"}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setProblem(""); }}
          className="text-sm text-slate-500"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function Centered({ children }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-100 p-6">
      <div className="bg-white p-6 rounded-xl shadow text-center max-w-sm">
        {children}
      </div>
    </div>
  );
}
