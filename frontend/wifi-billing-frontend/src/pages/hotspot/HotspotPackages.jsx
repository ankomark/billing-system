import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { fetchHotspotPackages } from "../../services/hotspot";

export default function HotspotPackages() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // MikroTik appends mac/ip; `t` identifies whose portal this is.
  const mac = searchParams.get("mac");
  const tenantToken = searchParams.get("t");

  useEffect(() => {
    if (!mac) {
      setLoading(false);
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
  }, [mac, tenantToken]);

  if (!mac) {
    return (
      <Centered>
        <h2 className="text-xl font-bold text-red-600 mb-2">Device not recognised</h2>
        <p className="text-slate-600">Please reconnect through the WiFi login page.</p>
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
      </div>
    </div>
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
