import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowLeft, Save, Loader } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Skeleton } from "../../components/ui/Skeleton";
import { fetchPackage, createPackage, updatePackage } from "../../services/packages";

const DURATION_UNITS = [
  { value: "minutes", label: "Minutes" },
  { value: "hours",   label: "Hours"   },
  { value: "days",    label: "Days"    },
  { value: "weeks",   label: "Weeks"   },
  { value: "months",  label: "Months"  },
  { value: "years",   label: "Years"   },
];

const EMPTY = {
  name: "", download_speed: "", upload_speed: "",
  duration_value: "", duration_unit: "days", price: "",
  monthly_data_cap_gb: 0, is_hotspot: false,
};

export default function PackageForm() {
  const { id }    = useParams();
  const navigate  = useNavigate();
  const qc        = useQueryClient();
  const isEdit    = Boolean(id);

  const [form, setForm]     = useState(EMPTY);
  const [saving, setSaving] = useState(false);

  const { data: existing, isLoading } = useQuery({
    queryKey: ["package", id],
    queryFn: () => fetchPackage(id),
    enabled: isEdit,
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    if (existing) {
      setForm({
        name:                existing.name                ?? "",
        download_speed:      existing.download_speed      ?? "",
        upload_speed:        existing.upload_speed        ?? "",
        duration_value:      existing.duration_value      ?? "",
        duration_unit:       existing.duration_unit       ?? "days",
        price:               existing.price               ?? "",
        monthly_data_cap_gb: existing.monthly_data_cap_gb ?? 0,
        is_hotspot:          existing.is_hotspot          ?? false,
      });
    }
  }, [existing]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setForm((prev) => ({ ...prev, [name]: type === "checkbox" ? checked : value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      if (isEdit) {
        await updatePackage(id, form);
        toast.success("Package updated");
        qc.invalidateQueries({ queryKey: ["package", id] });
      } else {
        await createPackage(form);
        toast.success("Package created");
      }
      qc.invalidateQueries({ queryKey: ["packages"] });
      navigate("/admin/packages");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to save package");
    } finally {
      setSaving(false);
    }
  };

  if (isEdit && isLoading) {
    return (
      <AdminLayout>
        <div className="max-w-xl space-y-4">
          <Skeleton className="h-8 w-48" />
          <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
            {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        </div>
      </AdminLayout>
    );
  }

  return (
    <AdminLayout>
      <div className="max-w-xl space-y-6">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/admin/packages")}
            className="text-slate-400 hover:text-slate-700 transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-slate-800">
              {isEdit ? "Edit Package" : "New Package"}
            </h1>
            <p className="text-slate-500 text-sm mt-0.5">
              {isEdit ? `Editing ${existing?.name}` : "Fill in the details for the new package"}
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-slate-200 p-6 space-y-5">
          <Field label="Package Name" name="name" value={form.name} onChange={handleChange} placeholder="e.g. Basic 5Mbps" required />

          <div className="grid grid-cols-2 gap-4">
            <Field label="Download Speed (Mbps)" type="number" name="download_speed" value={form.download_speed} onChange={handleChange} placeholder="e.g. 5"  required />
            <Field label="Upload Speed (Mbps)"   type="number" name="upload_speed"   value={form.upload_speed}   onChange={handleChange} placeholder="e.g. 2"  required />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Duration" type="number" name="duration_value" value={form.duration_value} onChange={handleChange} placeholder="e.g. 30" required />
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">Unit</label>
              <select
                name="duration_unit"
                value={form.duration_unit}
                onChange={handleChange}
                className="w-full border border-slate-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {DURATION_UNITS.map((u) => (
                  <option key={u.value} value={u.value}>{u.label}</option>
                ))}
              </select>
            </div>
          </div>

          <Field label="Price (KES)" type="number" name="price" value={form.price} onChange={handleChange} placeholder="e.g. 500" required />

          <div className="border-t border-slate-100 pt-5 space-y-4">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Advanced</p>

            <Field
              label="Monthly Data Cap (GB)"
              type="number"
              name="monthly_data_cap_gb"
              value={form.monthly_data_cap_gb}
              onChange={handleChange}
              placeholder="0 = unlimited"
              min="0"
            />

            <label className="flex items-center gap-3 cursor-pointer select-none">
              <div className="relative">
                <input
                  type="checkbox"
                  name="is_hotspot"
                  checked={form.is_hotspot}
                  onChange={handleChange}
                  className="sr-only peer"
                />
                <div className="w-10 h-6 bg-slate-200 rounded-full peer-checked:bg-blue-600 transition-colors" />
                <div className="absolute top-1 left-1 w-4 h-4 bg-white rounded-full shadow transition-transform peer-checked:translate-x-4" />
              </div>
              <div>
                <p className="text-sm font-medium text-slate-700">Hotspot package</p>
                <p className="text-xs text-slate-400">Only available for hotspot customers</p>
              </div>
            </label>
          </div>

          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-6 py-2.5 rounded-lg font-semibold text-sm transition-colors"
            >
              {saving ? <Loader size={14} className="animate-spin" /> : <Save size={14} />}
              {saving ? "Saving…" : isEdit ? "Save Changes" : "Create Package"}
            </button>
            <button
              type="button"
              onClick={() => navigate("/admin/packages")}
              className="px-6 py-2.5 border border-slate-300 rounded-lg text-sm text-slate-700 hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </AdminLayout>
  );
}

function Field({ label, name, value, onChange, type = "text", placeholder, required, min }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1.5">
        {label} {required && <span className="text-red-500">*</span>}
      </label>
      <input
        type={type}
        name={name}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        required={required}
        min={min}
        className="w-full border border-slate-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
      />
    </div>
  );
}
