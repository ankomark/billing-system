import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { fetchHotspotPackages } from "../../services/hotspot";

export default function HotspotPackages() {
  const [packages, setPackages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // ✅ MikroTik auto-appended variables
  const mac = searchParams.get("mac");
  const ip = searchParams.get("ip");
  const username = searchParams.get("username");

  useEffect(() => {
    fetchHotspotPackages()
      .then(setPackages)
      .finally(() => setLoading(false));
  }, []);

  // ❌ If MAC address missing — user bypassed hotspot or captive portal failed
  if (!mac) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-100 p-6">
        <div className="bg-white p-6 rounded shadow text-center">
          <h2 className="text-xl font-bold text-red-600 mb-2">
            Device Not Recognized
          </h2>
          <p className="text-gray-600">
            Please reconnect through the WiFi hotspot login page.
          </p>
        </div>
      </div>
    );
  }

  if (loading) {
    return <div className="p-4 text-center">Loading packages...</div>;
  }

  return (
    <div className="min-h-screen bg-gray-100 p-4">
      <h1 className="text-xl font-bold text-center mb-4">
        Select Internet Package
      </h1>

      {/* Show device identity */}
      <p className="text-center text-sm text-gray-600 mb-4">
        Device MAC: <span className="font-mono">{mac}</span>
      </p>

      <div className="grid gap-4">
        {packages.map((pkg) => (
          <div
            key={pkg.id}
            className="bg-white rounded shadow p-4 cursor-pointer hover:shadow-lg transition"
            onClick={() =>
              navigate(
                `/hotspot/pay?package=${pkg.id}&mac=${mac}&ip=${ip}&username=${username}`
              )
            }
          >
            <h2 className="font-semibold text-lg">{pkg.name}</h2>

            <p className="text-sm text-gray-600">
              Speed: {pkg.download_speed}/{pkg.upload_speed} Mbps
            </p>

            <p className="text-sm text-gray-600">
              Validity: {pkg.duration_days} days
            </p>

            <p className="font-bold text-blue-700 mt-2">
              KES {pkg.price}
            </p>

            <button className="mt-3 bg-blue-600 text-white w-full py-2 rounded">
              Select & Continue
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
