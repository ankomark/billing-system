import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import api from "../../services/api";

export default function UsageAlerts() {
  const [alerts, setAlerts]   = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/admin/usage/Alerts/")
      .then((res) => setAlerts(res.data || []))
      .catch(() => setAlerts([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Usage Alerts</h1>
          <p className="text-slate-500 text-sm mt-1">
            Customers at 80 %+ of their monthly data cap
          </p>
        </div>

        {loading ? (
          <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-400 text-sm">
            Loading alerts…
          </div>
        ) : alerts.length === 0 ? (
          <div className="bg-white rounded-xl border border-slate-200 p-10 text-center">
            <p className="text-3xl mb-3">✅</p>
            <p className="font-medium text-slate-700">All customers are within their data limits</p>
            <p className="text-slate-400 text-sm mt-1">No alerts at this time</p>
          </div>
        ) : (
          <div className="space-y-3">
            {alerts.map((a, i) => {
              const critical = a.percent >= 100;
              return (
                <div
                  key={i}
                  className={`flex items-center justify-between bg-white rounded-xl border p-4 ${
                    critical ? "border-red-200" : "border-amber-200"
                  }`}
                >
                  <div>
                    <p className="font-semibold text-slate-800">{a.customer}</p>
                    <p className="text-sm text-slate-500">{a.phone}</p>
                  </div>
                  <div className="text-right">
                    <p className={`text-lg font-bold ${critical ? "text-red-600" : "text-amber-600"}`}>
                      {a.percent}%
                    </p>
                    <p className="text-xs text-slate-500">
                      {a.used_gb} GB / {a.cap_gb} GB
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
