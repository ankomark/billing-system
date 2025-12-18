import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";
import {
  fetchCustomerDetail,
  suspendOrResumeCustomer,
  resendVoucher,
} from "../../services/customers";
import api from "../../services/api";

export default function CustomerDetail() {
  const { id } = useParams();
  const [customer, setCustomer] = useState(null);
  const [routers, setRouters] = useState([]);
  const [selectedRouter, setSelectedRouter] = useState("");
  const [loading, setLoading] = useState(false);

  const loadCustomer = async () => {
    const data = await fetchCustomerDetail(id);
    setCustomer(data);
  };

  const loadRouters = async () => {
    const res = await api.get("/admin/routers/");
    setRouters(res.data || []);
  };

  useEffect(() => {
    loadCustomer();
    loadRouters();
  }, [id]);

  if (!customer) {
    return (
      <AdminLayout>
        <div className="p-6">Loading customer details...</div>
      </AdminLayout>
    );
  }

  // -----------------------------
  // ACTIONS
  // -----------------------------
  const handleSuspendResume = async (action) => {
    if (action === "suspend") {
      const ok = window.confirm("Suspend this customer?");
      if (!ok) return;
    }

    setLoading(true);
    await suspendOrResumeCustomer(customer.id, action);
    await loadCustomer();
    setLoading(false);
  };

  const handleResendVoucher = async () => {
    setLoading(true);
    try {
      await resendVoucher(customer.id);
      alert("✅ Voucher sent to customer");
    } catch {
      alert("❌ Failed to send voucher");
    }
    setLoading(false);
  };

  const handleAutoMigrate = async () => {
    const ok = window.confirm(
      "Automatically migrate customer to best available router?"
    );
    if (!ok) return;

    setLoading(true);
    try {
      await api.post("/admin/customers/migrate/", {
        customer_id: customer.id,
      });
      alert("✅ Customer migrated automatically");
      await loadCustomer();
    } catch (err) {
      alert(err.response?.data?.detail || "❌ Migration failed");
    }
    setLoading(false);
  };

  const handleManualMigrate = async () => {
    if (!selectedRouter) {
      alert("Select a router first");
      return;
    }

    const ok = window.confirm(
      "Manually migrate customer to selected router?"
    );
    if (!ok) return;

    setLoading(true);
    try {
      await api.post("/admin/customers/migrate/", {
        customer_id: customer.id,
        router_id: selectedRouter,
      });
      alert("✅ Customer migrated successfully");
      await loadCustomer();
    } catch (err) {
      alert(err.response?.data?.detail || "❌ Migration failed");
    }
    setLoading(false);
  };

  return (
    <AdminLayout>
      <div className="p-6 space-y-6">
        {/* HEADER */}
        <h1 className="text-2xl font-bold">{customer.full_name}</h1>

        {/* BASIC INFO */}
        <div className="bg-white p-4 rounded shadow">
          <p><b>Phone:</b> {customer.phone}</p>
          <p><b>Connection:</b> {customer.connection_type}</p>
          <p>
            <b>Status:</b>{" "}
            <span
              className={
                customer.status === "active"
                  ? "text-green-600 font-semibold"
                  : "text-red-600 font-semibold"
              }
            >
              {customer.status}
            </span>
          </p>
          <p>
            <b>Assigned Router:</b>{" "}
            {customer.router_name || "Not assigned"}
          </p>

          {/* ACTION BUTTONS */}
          <div className="flex flex-wrap gap-3 mt-4">
            {customer.status === "active" ? (
              <button
                disabled={loading}
                onClick={() => handleSuspendResume("suspend")}
                className="bg-red-600 text-white px-4 py-2 rounded"
              >
                Suspend
              </button>
            ) : (
              <button
                disabled={loading}
                onClick={() => handleSuspendResume("resume")}
                className="bg-green-600 text-white px-4 py-2 rounded"
              >
                Resume
              </button>
            )}

            <button
              disabled={loading}
              onClick={handleResendVoucher}
              className="bg-blue-600 text-white px-4 py-2 rounded"
            >
              Resend Voucher
            </button>
          </div>
        </div>

        {/* ROUTER MIGRATION */}
        <div className="bg-white p-4 rounded shadow">
          <h2 className="text-lg font-semibold mb-3">
            Router Migration
          </h2>

          <div className="flex flex-col md:flex-row gap-4">
            <button
              disabled={loading}
              onClick={handleAutoMigrate}
              className="bg-purple-600 text-white px-4 py-2 rounded"
            >
              Auto Failover Migrate
            </button>

            <div className="flex gap-2">
              <select
                value={selectedRouter}
                onChange={(e) => setSelectedRouter(e.target.value)}
                className="border rounded px-3 py-2"
              >
                <option value="">Select Router</option>
                {routers.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} {r.is_online ? "(online)" : "(offline)"}
                  </option>
                ))}
              </select>

              <button
                disabled={loading}
                onClick={handleManualMigrate}
                className="bg-orange-600 text-white px-4 py-2 rounded"
              >
                Manual Migrate
              </button>
            </div>
          </div>
        </div>

        {/* SUBSCRIPTIONS */}
        <div>
          <h2 className="text-lg font-semibold mb-2">Subscriptions</h2>

          {customer.subscriptions.length === 0 && (
            <p className="text-gray-500">No subscriptions</p>
          )}

          {customer.subscriptions.map((s) => (
            <div key={s.id} className="bg-white p-4 mb-2 rounded shadow">
              <p><b>Package:</b> {s.package_name}</p>
              <p><b>Expires:</b> {s.expiry_date}</p>
              <p><b>Status:</b> {s.status}</p>
            </div>
          ))}
        </div>

        {/* VOUCHERS */}
        <div>
          <h2 className="text-lg font-semibold mb-2">Vouchers</h2>

          {customer.vouchers.length === 0 && (
            <p className="text-gray-500">No vouchers</p>
          )}

          {customer.vouchers.map((v) => (
            <div key={v.code} className="bg-gray-100 p-2 rounded mb-1">
              <b>{v.code}</b> — expires {v.expires_at}
            </div>
          ))}
        </div>
      </div>
    </AdminLayout>
  );
}
