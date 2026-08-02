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
  // Selling at the counter. Optional: leave the package unset and this
  // behaves exactly as it did.
  package: "",
  paid_with: "",
  payment_reference: "",
};

function Field({ label, required, error, children }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-300 mb-1.5">
        {label} {required && <span className="text-red-500">*</span>}
      </label>
      {children}
      {error && <p className="mt-1 text-xs text-red-300">{error}</p>}
    </div>
  );
}

function Input({ className = "", ...props }) {
  return (
    <input
      className={`w-full border border-white/15 bg-slate-950 text-slate-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-white/5 disabled:text-slate-500 ${className}`}
      {...props}
    />
  );
}

function Select({ children, className = "", ...props }) {
  return (
    <select
      className={`w-full border border-white/15 bg-slate-950 text-slate-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-slate-900/80 disabled:bg-white/5 ${className}`}
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

  // The catalogue, for selling at the counter. Only hotspot packages: a
  // walk-in buying an hour of WiFi is not renewing a home line.
  const { data: packageData } = useQuery({
    queryKey: ["packages", "hotspot"],
    queryFn: async () => (await api.get("packages/", { params: { page_size: 100 } })).data,
    staleTime: 5 * 60 * 1000,
  });
  // Archived packages are retired from sale, so they are not offered here
  // either — the server refuses them, and a select that lists one is a select
  // that produces an error.
  const hotspotPackages = (packageData?.results ?? []).filter(
    (p) => p.is_hotspot && !p.is_archived
  );

  const [issued, setIssued] = useState(null);

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
      // Creating a hotspot customer used to leave them with no subscription
      // and no voucher — active, with no access, and nothing on screen to
      // give them. Sending a package makes the sale in the same request.
      if (!isEdit && form.package) {
        payload.package = Number(form.package);
        if (form.paid_with) {
          payload.paid_with = form.paid_with;
          if (form.payment_reference) {
            payload.payment_reference = form.payment_reference.trim();
          }
        }
      }
    }

    setSaving(true);
    try {
      if (isEdit) {
        await updateCustomer(id, payload);
        toast.success("Customer updated successfully");
        qc.invalidateQueries({ queryKey: ["customer", id] });
      } else {
        const created = await createCustomer(payload);
        qc.invalidateQueries({ queryKey: ["customers"] });

        if (created.provisioning_error) {
          // The customer exists; the sale did not complete. Say which, rather
          // than a success toast over a half-finished job.
          toast.error(`Customer created, but no access: ${created.provisioning_error}`);
        } else if (created.voucher_code) {
          // Hold them here with the code on screen. Navigating away from the
          // one moment it is visible is how a counter sale ends with a
          // customer holding nothing.
          setIssued({ code: created.voucher_code, id: created.id });
          setSaving(false);
          return;
        } else {
          toast.success("Customer created successfully");
        }
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
          <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-6 space-y-4">
            {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        </div>
      </AdminLayout>
    );
  }

  /**
   * The code, on screen, before anything navigates.
   *
   * A counter sale ends with the operator reading a code out to the person in
   * front of them. Going straight to the customer page after creating them
   * meant the one moment it was visible was also the moment it was replaced.
   */
  if (issued) {
    return (
      <AdminLayout>
        <div className="max-w-md">
          <div className="rounded-2xl border border-emerald-500/25 bg-emerald-500/10 p-6 text-center">
            <p className="text-xs font-semibold uppercase tracking-wider text-emerald-300">
              Access code
            </p>
            <p className="my-3 select-all font-mono text-3xl font-bold tracking-wider text-white">
              {issued.code}
            </p>
            <p className="text-sm text-emerald-200">
              Read this out to the customer. It works on the first device that
              uses it, and only that one.
            </p>
          </div>

          <div className="mt-4 flex gap-3">
            <button
              onClick={() => navigate(`/admin/customers/${issued.id}`)}
              className="flex-1 rounded-xl bg-blue-600 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-700"
            >
              Open customer
            </button>
            <button
              onClick={() => {
                setIssued(null);
                setForm({ ...INITIAL, connection_type: "hotspot" });
              }}
              className="flex-1 rounded-xl border border-white/15 py-2.5 text-sm font-semibold text-slate-200 transition-colors hover:bg-white/5"
            >
              Sell another
            </button>
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
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">
              {isEdit ? "Edit Customer" : "Add Customer"}
            </h1>
            <p className="text-slate-400 text-sm mt-0.5">
              {isEdit ? `Editing ${existing?.full_name}` : "Create a new customer account"}
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-6 space-y-5">

            {/* Basic info */}
            <div className="pb-1">
              <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] mb-4">
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
            <div className="border-t border-white/5 pt-5">
              <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] mb-4">
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
                  <>
                    <Field label="Device MAC" error={errors.hotspot_username}>
                      <Input
                        value={form.hotspot_username}
                        onChange={set("hotspot_username")}
                        placeholder="Leave blank — bound on first use"
                      />
                    </Field>

                    {!isEdit && (
                      <>
                        <Field label="Package" error={errors.package}>
                          <Select value={form.package} onChange={set("package")}>
                            <option value="">None — create the customer only</option>
                            {hotspotPackages.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name} · KES {Number(p.price).toLocaleString()}
                              </option>
                            ))}
                          </Select>
                        </Field>

                        {form.package && (
                          <>
                            <Field label="Paid with" error={errors.paid_with}>
                              <Select value={form.paid_with} onChange={set("paid_with")}>
                                <option value="">Not paid yet — invoice only</option>
                                <option value="cash">Cash</option>
                                <option value="mpesa">M-Pesa</option>
                                <option value="bank">Bank</option>
                              </Select>
                            </Field>
                            {form.paid_with === "mpesa" && (
                              <Field label="M-Pesa receipt" error={errors.payment_reference}>
                                <Input
                                  value={form.payment_reference}
                                  onChange={set("payment_reference")}
                                  placeholder="TGX11AA001"
                                />
                              </Field>
                            )}
                          </>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            </div>

            {/* Advanced */}
            <div className="border-t border-white/5 pt-5">
              <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] mb-4">
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
              className="px-6 py-2.5 border border-white/15 rounded-lg text-sm font-medium text-slate-300 hover:bg-white/5 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </AdminLayout>
  );
}
