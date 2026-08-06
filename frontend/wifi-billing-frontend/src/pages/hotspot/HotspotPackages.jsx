import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  fetchHotspotPackages,
  fetchHotspotDeviceStatus,
  reconnectHotspotDevice,
  validateHotspotVoucher,
} from "../../services/hotspot";
import { PLATFORM_NAME } from "../../constants/brand";

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
 *
 * Everything here is built for someone standing up, on a phone, with no
 * internet except this page. That is why the redeem box sits near the top
 * rather than at the bottom — a customer who has already paid and is not
 * online is the most stuck person who reaches this screen, and scrolling past
 * a price list to find help is the wrong order.
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
        <h2 className="mb-2 text-xl font-bold text-red-600">Device not recognised</h2>
        <p className="text-slate-600">Please reconnect through the WiFi login page.</p>
      </Centered>
    );
  }

  if (checking) {
    return (
      <Centered>
        <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-[3px] border-slate-200 border-t-blue-600" />
        <p className="font-medium text-slate-700">Checking your device…</p>
        <p className="mt-1 text-xs text-slate-500">
          If you already have time left, we'll put you straight back online.
        </p>
      </Centered>
    );
  }

  if (error) {
    return (
      <Centered>
        <h2 className="mb-2 text-xl font-bold text-red-600">Something went wrong</h2>
        <p className="text-slate-600">{error}</p>
      </Centered>
    );
  }

  const packages = data?.results ?? [];

  return (
    <div className="min-h-screen bg-slate-100">
      <div className="mx-auto w-full max-w-2xl px-4 py-5 sm:py-8">
        {/* No images above the fold, deliberately.
            There was a banner slot here. It was lazy and outside the data
            path, so it did not block the packages — but on a captive portal
            the visitor's only working route is the walled garden, and the
            fastest image is still the one nobody asks for. */}
        <header className="mb-4 text-center">
          <h1 className="text-lg font-bold text-slate-800 sm:text-xl">
            {data?.provider || "WiFi"}
          </h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Pick a package and pay with M-Pesa. You'll be online in under a minute.
          </p>
        </header>

        <RedeemPanel mac={mac} tenantToken={tenantToken} navigate={navigate} />

        <h2 className="mb-3 mt-6 text-center text-sm font-semibold uppercase tracking-wider text-slate-500">
          Packages
        </h2>

        {loading ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-[86px] animate-pulse rounded-2xl bg-slate-200" />
            ))}
          </div>
        ) : packages.length === 0 ? (
          <div className="rounded-2xl bg-white p-6 text-center text-sm text-slate-500">
            No packages are available right now. Please ask at the counter.
          </div>
        ) : (
          /* One column on a phone, two once there is room. A price list is a
             set of choices to compare, so on anything wider than a phone
             stacking them makes the comparison a scroll. */
          <div className="grid gap-3 sm:grid-cols-2">
            {packages.map((pkg) => (
              <button
                key={pkg.id}
                onClick={() =>
                  navigate(
                    `/hotspot/pay?package=${pkg.id}&mac=${encodeURIComponent(mac)}` +
                      (tenantToken ? `&t=${encodeURIComponent(tenantToken)}` : "")
                  )
                }
                className="w-full rounded-2xl bg-white p-4 text-left shadow-sm transition-shadow
                           hover:shadow-md focus:outline-none focus:ring-2 focus:ring-blue-500
                           active:scale-[.99]"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-slate-800">{pkg.name}</p>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {pkg.download_speed}/{pkg.upload_speed} Mbps · {pkg.duration}
                    </p>
                    {pkg.monthly_data_cap_gb > 0 && (
                      <p className="mt-0.5 text-xs text-slate-400">
                        {pkg.monthly_data_cap_gb} GB included
                      </p>
                    )}
                  </div>
                  <p className="whitespace-nowrap text-lg font-bold text-blue-600">
                    KES {Number(pkg.price).toLocaleString()}
                  </p>
                </div>
              </button>
            ))}
          </div>
        )}

        <HowItWorks provider={data?.provider} support={data?.support_phone} mac={mac} />
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
        className="flex w-full items-center justify-center gap-2 rounded-2xl border border-slate-300
                   bg-white py-3.5 text-sm font-semibold text-slate-700 shadow-sm
                   transition-colors hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
          <path d="M15 7h3a5 5 0 0 1 0 10h-3M9 17H6A5 5 0 0 1 6 7h3M8 12h8" />
        </svg>
        Already paid? Use your code
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="rounded-2xl bg-white p-4 shadow-sm">
      <label htmlFor="redeem" className="block text-sm font-semibold text-slate-800">
        Paste your M-Pesa message
      </label>
      <p className="mb-2 mt-0.5 text-xs leading-relaxed text-slate-500">
        Or type your access code. The whole message is fine — we'll find the
        code in it.
      </p>
      <textarea
        id="redeem"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        autoFocus
        autoCapitalize="characters"
        spellCheck="false"
        placeholder="TGX11AA001 Confirmed. Ksh50.00 sent to…"
        className="w-full resize-none rounded-xl border border-slate-300 px-3 py-2.5 text-sm
                   focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      {problem && <p className="mt-2 text-sm text-red-600">{problem}</p>}
      <div className="mt-3 flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="flex-1 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white
                     transition-colors hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? "Checking…" : "Connect me"}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setProblem(""); }}
          className="px-2 text-sm text-slate-500"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

/**
 * The guide at the foot of the page.
 *
 * Written for the two questions people actually arrive with — how do I buy,
 * and I paid so why am I not online — rather than a feature list. Last on the
 * page because someone who knows what they are doing should never have to
 * scroll past it.
 */
function HowItWorks({ provider, support, mac }) {
  const steps = [
    ["Pick a package", "Tap one above. Prices include everything."],
    ["Enter your M-Pesa number", "You'll get a prompt on your phone — approve it."],
    ["You're online", "It happens by itself. Keep the code we show you."],
  ];

  return (
    <section className="mt-8 border-t border-slate-200 pt-6">
      <h2 className="mb-3 text-sm font-bold text-slate-800">How it works</h2>
      <ol className="space-y-3">
        {steps.map(([title, body], i) => (
          <li key={title} className="flex gap-3">
            <span
              className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full
                         bg-blue-600 text-xs font-bold text-white"
              aria-hidden="true"
            >
              {i + 1}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-800">{title}</p>
              <p className="text-xs leading-relaxed text-slate-500">{body}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-5 rounded-xl bg-amber-50 p-4">
        <p className="text-sm font-semibold text-amber-900">Paid but not online?</p>
        <p className="mt-1 text-xs leading-relaxed text-amber-800">
          Use <strong>Already paid? Use your code</strong> at the top of this
          page and paste the M-Pesa message Safaricom sent you. That reconnects
          you without paying again.
        </p>
      </div>

      <div className="mt-5 space-y-1.5 text-xs text-slate-500">
        <p>
          <strong className="text-slate-600">Your time keeps running</strong> even
          if you disconnect. Come back to this page and you'll be let straight
          back on until it expires.
        </p>
        <p>
          <strong className="text-slate-600">Your code belongs to this phone.</strong>{" "}
          It won't work on a different one.
        </p>
      </div>

      <footer className="mt-6 border-t border-slate-200 pt-4 text-center">
        {support && (
          <p className="text-xs text-slate-500">
            Need help? Call{" "}
            <a href={`tel:${support}`} className="font-semibold text-blue-600">
              {support}
            </a>
          </p>
        )}
        <p className="mt-1 font-mono text-[10px] text-slate-400">{mac}</p>
        <p className="mt-2 text-[10px] text-slate-400">
          {provider ? `${provider} · ` : ""}Billing by {PLATFORM_NAME}
        </p>
      </footer>
    </section>
  );
}

function Centered({ children }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 p-6">
      <div className="max-w-sm rounded-2xl bg-white p-6 text-center shadow">
        {children}
      </div>
    </div>
  );
}
