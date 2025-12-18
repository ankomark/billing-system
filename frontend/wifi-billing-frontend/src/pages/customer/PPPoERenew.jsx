import { useState, useEffect } from "react";
import api from "../../services/api";
import { useNavigate } from "react-router-dom";

export default function PPPoERenew() {
  const [packages, setPackages] = useState([]);
  const [phone, setPhone] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();

  // Load packages
  useEffect(() => {
    api.get("packages/").then((res) => setPackages(res.data));
  }, []);

  const handleRenew = async () => {
    if (!selected || !phone) return alert("Missing package or phone number");

    setLoading(true);

    try {
      const res = await api.post("pppoe/renew/", {
        package_id: selected.id,
        phone,
      });

      navigate(
        `/customer/pppoe/status?subscription=${res.data.subscription_id}`
      );

    } catch (err) {
      alert("Payment failed to start");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 max-w-lg mx-auto">
      <h1 className="text-2xl font-bold mb-4">Renew PPPoE Subscription</h1>

      <label>M-Pesa Phone</label>
      <input
        type="tel"
        className="w-full border p-3 rounded mb-4"
        placeholder="2547XXXXXXXX"
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
      />

      <h2 className="font-semibold mb-2">Choose Package</h2>

      {packages.map((pkg) => (
        <div
          key={pkg.id}
          onClick={() => setSelected(pkg)}
          className={`border p-3 mb-2 rounded cursor-pointer ${
            selected?.id === pkg.id ? "border-blue-600" : "border-gray-300"
          }`}
        >
          <b>{pkg.name}</b> — {pkg.duration_days} days  
          <br />
          Speed: {pkg.download_speed}/{pkg.upload_speed} Mbps  
          <br />
          <b>KES {pkg.price}</b>
        </div>
      ))}

      <button
        disabled={loading}
        onClick={handleRenew}
        className="w-full bg-blue-600 text-white py-2 rounded mt-4"
      >
        {loading ? "Processing…" : "Renew Now"}
      </button>
    </div>
  );
}
