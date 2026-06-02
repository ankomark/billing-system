import { useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowLeft, Save, Loader } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Skeleton } from "../../components/ui/Skeleton";
import { fetchCustomerDetail, createCustomer, updateCustomer } from "../../services/customers";
import api from "../../services/api";

const INITIAL = {
  full_name: "",
  phone: "",
  connection_type: "pppoe",
  pppoe_username: "",
  pppoe_password: "",
  hotspot_username: "",
  router: "",
  custom_data_cap_gb: "",
  status: "active",
};

function Field({ label, required, error, children }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1.5">
        {label} {required && <span className="text-red-500">*</span>}
      </label>
      {children}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}

function Input({ className = "", ...props }) {
  return (
    <input
      className={`w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-slate-50 disabled:text-slate-400 ${className}`}
      {...props}
    />
  );
}

function Select({ children, className = "", ...props }) {
  return (
    <select
      className={`w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white disabled:bg-slate-50 ${className}`}
      {...props}
    >
      {children}
    </select>
  );
}

function validate(form) {
  const errors = {};
  if (!form.full_name.trim()) errors.full_name = "Full name is required";
  if (!form.phone.trim()) {
    errors.phone = "Phone number is required";
  } else if (!/^(\+?254|0)[17]\d{8}$/.test(form.phone.replace(/\s/g, ""))) {
    errors.phone = "Enter a valid Kenyan phone number (e.g. 0712345678)";
  }
  if (!form.connection_type) errors.connection_type = "Connection type is required";
  if (form.custom_data_cap_gb && isNaN(Number(form.custom_data_cap_gb))) {
    errors.custom_data_cap_gb = "Must be a number";
  }
  return errors;
}

export default function CustomerForm() {
  const { id }   = useParams();
  const navigate = useNavigate();
  const qc       = useQueryClient();
  const isEdit   = Boolean(id);

  const [form, setForm]       = useState(INITIAL);
  const [errors, setErrors]   = useState({});
  const [saving, setSaving]   = useState(false);

  // Load existing customer for edit mode
  const { data: existing, isLoading: loadingCustomer } = useQuery({
    queryKey: ["customer", id],
    queryFn: () => fetchCustomerDetail(id),
    enabled: isEdit,
  });

  // Load routers for assignment
  const { data: routersData } = useQuery({
    queryKey: ["routers-list"],
    queryFn: () => api.get("admin/routers/").then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });
  const routers = Array.isArray(routersData) ? routersData : routersData?.results ?? [];

  // Populate form when editing
  useEffect(() => {
    if (existing) {
      setForm({
        full_name:          existing.full_name       ?? "",
        phone:              existing.phone            ?? "",
        connection_type:    existing.connection_type ?? "pppoe",
        pppoe_username:     existing.pppoe_username  ?? "",
        pppoe_password:     "",
        hotspot_username:   existing.hotspot_username ?? "",
        router:             existing.router?.toString() ?? "",
        custom_data_cap_gb: existing.custom_data_cap_gb?.toString() ?? "",
        status:             existing.status           ?? "active",
      });
    }
  }, [existing]);

  const set = (field) => (e) => {
    setForm((f) => ({ ...f, [field]: e.target.value }));
    if (errors[field]) setErrors((e) => { const n = { ...e }; delete n[field]; return n; });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const errs = validate(form);
    if (Object.keys(errs).length) { setErrors(errs); return; }

    const payload = {
      full_name:       form.full_name.trim(),
      phone:           form.phone.trim(),
      connection_type: form.connection_type,
      status:          form.status,
    };

    if (form.router)             payload.router = Number(form.router);
    if (form.custom_data_cap_gb) payload.custom_data_cap_gb = Number(form.custom_data_cap_gb);

    if (form.connection_type === "pppoe") {
      if (form.pppoe_username) payload.pppoe_username = form.pppoe_username.trim();
      if (form.pppoe_password) payload.pppoe_password = form.pppoe_password;
    } else {
      if (form.hotspot_username) payload.hotspot_username = form.hotspot_username.trim();
    }

    setSaving(true);
    try {
      if (isEdit) {
        await updateCustomer(id, payload);
        toast.success("Customer updated successfully");
        qc.invalidateQueries({ queryKey: ["customer", id] });
      } else {
        const created = await createCustomer(payload);
        toast.success("Customer created successfully");
        qc.invalidateQueries({ queryKey: ["customers"] });
        navigate(`/admin/customers/${created.id}`);
        return;
      }
      qc.invalidateQueries({ queryKey: ["customers"] });
      navigate(`/admin/customers/${id}`);
    } catch (err) {
      const data = err.response?.data;
      if (data && typeof data === "object" && !data.detail) {
        // DRF field-level errors
        const fieldErrs = {};
        for (const [k, v] of Object.entries(data)) {
          fieldErrs[k] = Array.isArray(v) ? v[0] : v;
        }
        setErrors(fieldErrs);
        toast.error("Please fix the errors below");
      } else {
        toast.error(data?.detail || "Failed to save customer");
      }
    } finally {
      setSaving(false);
    }
  };

  if (isEdit && loadingCustomer) {
    return (
      <AdminLayout>
        <div className="max-w-2xl space-y-4">
          <Skeleton className="h-8 w-48" />
          <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
            {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        </div>
      </AdminLayout>
    );
  }

  return (
    <AdminLayout>
      <div className="max-w-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => navigate(isEdit ? `/admin/customers/${id}` : "/admin/customers")}
            className="text-slate-400 hover:text-slate-700 transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-slate-800">
              {isEdit ? "Edit Customer" : "Add Customer"}
            </h1>
            <p className="text-slate-500 text-sm mt-0.5">
              {isEdit ? `Editing ${existing?.full_name}` : "Create a new customer account"}
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-5">

            {/* Basic info */}
            <div className="pb-1">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">
                Basic Information
              </p>
              <div className="grid sm:grid-cols-2 gap-4">
                <Field label="Full Name" required error={errors.full_name}>
                  <Input
                    value={form.full_name}
                    onChange={set("full_name")}
                    placeholder="John Doe"
                    autoFocus={!isEdit}
                  />
                </Field>
                <Field label="Phone Number" required error={errors.phone}>
                  <Input
                    value={form.phone}
                    onChange={set("phone")}
                    placeholder="0712345678"
                    type="tel"
                  />
                </Field>
              </div>
            </div>

            {/* Connection */}
            <div className="border-t border-slate-100 pt-5">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">
                Connection
              </p>
              <div className="grid sm:grid-cols-2 gap-4">
                <Field label="Connection Type" required error={errors.connection_type}>
                  <Select value={form.connection_type} onChange={set("connection_type")}>
                    <option value="pppoe">PPPoE</option>
                    <option value="hotspot">Hotspot</option>
                  </Select>
                </Field>
                <Field label="Assign Router" error={errors.router}>
                  <Select value={form.router} onChange={set("router")}>
                    <option value="">Auto-assign</option>
                    {routers.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} {r.online || r.is_online ? "" : "(offline)"}
                      </option>
                    ))}
                  </Select>
                </Field>

                {form.connection_type === "pppoe" && (
                  <>
                    <Field label="PPPoE Username" error={errors.pppoe_username}>
                      <Input
                        value={form.pppoe_username}
                        onChange={set("pppoe_username")}
                        placeholder="Leave blank to auto-generate"
                      />
                    </Field>
                    <Field
                      label={isEdit ? "New PPPoE Password" : "PPPoE Password"}
                      error={errors.pppoe_password}
                    >
                      <Input
                        type="password"
                        value={form.pppoe_password}
                        onChange={set("pppoe_password")}
                        placeholder={isEdit ? "Leave blank to keep existing" : "Auto-generated if blank"}
                        autoComplete="new-password"
                      />
                    </Field>
                  </>
                )}

                {form.connection_type === "hotspot" && (
                  <Field label="Hotspot Username / MAC" error={errors.hotspot_username}>
                    <Input
                      value={form.hotspot_username}
                      onChange={set("hotspot_username")}
                      placeholder="Bound on first use"
                    />
                  </Field>
                )}
              </div>
            </div>

            {/* Advanced */}
            <div className="border-t border-slate-100 pt-5">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">
                Advanced
              </p>
              <div className="grid sm:grid-cols-2 gap-4">
                <Field label="Custom Data Cap (GB)" error={errors.custom_data_cap_gb}>
                  <Input
                    type="number"
                    min="0"
                    value={form.custom_data_cap_gb}
                    onChange={set("custom_data_cap_gb")}
                    placeholder="0 = use package default"
                  />
                </Field>
                <Field label="Status" error={errors.status}>
                  <Select value={form.status} onChange={set("status")}>
                    <option value="active">Active</option>
                    <option value="expired">Expired</option>
                  </Select>
                </Field>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3 mt-5">
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-6 py-2.5 rounded-lg text-sm font-semibold transition-colors"
            >
              {saving ? <Loader size={14} className="animate-spin" /> : <Save size={14} />}
              {saving ? "Saving…" : isEdit ? "Save Changes" : "Create Customer"}
            </button>
            <button
              type="button"
              onClick={() => navigate(isEdit ? `/admin/customers/${id}` : "/admin/customers")}
              className="px-6 py-2.5 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </AdminLayout>
  );
}
