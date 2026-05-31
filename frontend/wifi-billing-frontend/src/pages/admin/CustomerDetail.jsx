import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchCustomerDetail, suspendOrResumeCustomer, resendVoucher } from "../../services/customers";
import api from "../../services/api";

function Badge({ status }) {
  const map = {
    active:    "bg-emerald-50 text-emerald-700 border-emerald-200",
    expired:   "bg-red-50 text-red-700 border-red-200",
    suspended: "bg-amber-50 text-amber-700 border-amber-200",
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border capitalize ${map[status] || "bg-slate-100 text-slate-600 border-slate-200"}`}>
      {status}
    </span>
  );
}

export default function CustomerDetail() {
  const { id }     = useParams();
  const navigate   = useNavigate();
  const [customer, setCustomer]       = useState(null);
  const [routers, setRouters]         = useState([]);
  const [selectedRouter, setSelectedRouter] = useState("");
  const [loading, setLoading]         = useState(false);
  const [toast, setToast]             = useState("");

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(""), 3000); };

  const loadCustomer = async () => { setCustomer(await fetchCustomerDetail(id)); };
  const loadRouters  = async () => {
    const res = await api.get("/admin/routers/");
    setRouters(Array.isArray(res.data) ? res.data : res.data.results || []);
  };

  useEffect(() => { loadCustomer(); loadRouters(); }, [id]);

  if (!customer) {
    return (
      <AdminLayout>
        <div className="flex items-center justify-center h-48 text-slate-400 text-sm">Loading customer…</div>
      </AdminLayout>
    );
  }

  const handleSuspendResume = async (action) => {
    if (action === "suspend" && !window.confirm("Suspend this customer?")) return;
    setLoading(true);
    await suspendOrResumeCustomer(customer.id, action);
    await loadCustomer();
    setLoading(false);
    showToast(action === "suspend" ? "Customer suspended" : "Customer resumed");
  };

  const handleResendVoucher = async () => {
    setLoading(true);
    try { await resendVoucher(customer.id); showToast("Voucher sent"); }
    catch { showToast("Failed to send voucher"); }
    setLoading(false);
  };

  const handleMigrate = async (routerId) => {
    if (!window.confirm(routerId ? "Migrate to selected router?" : "Auto-migrate to best router?")) return;
    setLoading(true);
    try {
      await api.post("/admin/customers/migrate/", { customer_id: customer.id, ...(routerId && { router_id: routerId }) });
      await loadCustomer();
      showToast("Migration successful");
    } catch (err) {
      showToast(err.response?.data?.detail || "Migration failed");
    }
    setLoading(false);
  };

  return (
    <AdminLayout>
      <div className="space-y-6 max-w-3xl">
        {/* Toast */}
        {toast && (
          <div className="fixed top-4 right-4 bg-slate-800 text-white px-4 py-2 rounded-lg text-sm shadow-lg z-50">
            {toast}
          </div>
        )}

        {/* Header */}
        <div className="flex items-center gap-3">
          <button onClick={() => navigate("/admin/customers")} className="text-slate-400 hover:text-slate-700 transition-colors text-sm">
            ← Back
          </button>
          <div>
            <h1 className="text-2xl font-bold text-slate-800">{customer.full_name}</h1>
            <p className="text-slate-500 text-sm mt-0.5">Customer #{customer.id}</p>
          </div>
        </div>

        {/* Info card */}
        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-4">Profile</h2>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <InfoRow label="Phone"      value={customer.phone} />
            <InfoRow label="Connection" value={customer.connection_type} />
            <div>
              <p className="text-slate-400 text-xs mb-1">Status</p>
              <Badge status={customer.status} />
            </div>
            <InfoRow label="Router" value={customer.router_name || "Not assigned"} />
          </div>

          {/* Actions */}
          <div className="flex flex-wrap gap-2 mt-5 pt-4 border-t border-slate-100">
            {customer.status === "active" ? (
              <Btn color="red" onClick={() => handleSuspendResume("suspend")} disabled={loading}>Suspend</Btn>
            ) : (
              <Btn color="green" onClick={() => handleSuspendResume("resume")} disabled={loading}>Resume</Btn>
            )}
            {customer.connection_type === "hotspot" && (
              <Btn color="blue" onClick={handleResendVoucher} disabled={loading}>Resend Voucher</Btn>
            )}
          </div>
        </div>

        {/* Router migration */}
        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-4">Router Migration</h2>
          <div className="flex flex-wrap gap-3 items-center">
            <Btn color="purple" onClick={() => handleMigrate(null)} disabled={loading}>
              Auto Failover
            </Btn>
            <select
              value={selectedRouter}
              onChange={(e) => setSelectedRouter(e.target.value)}
              className="border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Select router…</option>
              {routers.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} {r.online ? "(online)" : "(offline)"}
                </option>
              ))}
            </select>
            <Btn color="orange" onClick={() => { if (!selectedRouter) { showToast("Select a router first"); return; } handleMigrate(selectedRouter); }} disabled={loading || !selectedRouter}>
              Manual Migrate
            </Btn>
          </div>
        </div>

        {/* Subscriptions */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-100">
            <h2 className="text-sm font-semibold text-slate-700">Subscriptions</h2>
          </div>
          {customer.subscriptions?.length === 0 ? (
            <p className="px-5 py-4 text-slate-400 text-sm">No subscriptions</p>
          ) : (
            customer.subscriptions?.map((s) => (
              <div key={s.id} className="px-5 py-4 border-b border-slate-100 last:border-0 text-sm flex items-center justify-between">
                <div>
                  <p className="font-medium text-slate-800">{s.package_name}</p>
                  <p className="text-slate-400 text-xs mt-0.5">Expires {new Date(s.expiry_date).toLocaleDateString()}</p>
                </div>
                <Badge status={s.status} />
              </div>
            ))
          )}
        </div>

        {/* Vouchers */}
        {customer.vouchers?.length > 0 && (
          <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-100">
              <h2 className="text-sm font-semibold text-slate-700">Vouchers</h2>
            </div>
            {customer.vouchers.map((v) => (
              <div key={v.code} className="px-5 py-3 border-b border-slate-100 last:border-0 flex items-center justify-between text-sm">
                <code className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded text-xs">{v.code}</code>
                <span className="text-slate-400 text-xs">Expires {new Date(v.expires_at).toLocaleDateString()}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </AdminLayout>
  );
}

function InfoRow({ label, value }) {
  return (
    <div>
      <p className="text-slate-400 text-xs mb-0.5">{label}</p>
      <p className="font-medium text-slate-800 capitalize">{value || "—"}</p>
    </div>
  );
}

function Btn({ color, children, onClick, disabled }) {
  const colors = {
    red:    "bg-red-600 hover:bg-red-700",
    green:  "bg-emerald-600 hover:bg-emerald-700",
    blue:   "bg-blue-600 hover:bg-blue-700",
    purple: "bg-violet-600 hover:bg-violet-700",
    orange: "bg-orange-600 hover:bg-orange-700",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`${colors[color]} text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors`}
    >
      {children}
    </button>
  );
}
