import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchPackage, createPackage, updatePackage } from "../../services/packages";

const DURATION_UNITS = [
  { value: "minutes", label: "Minutes" },
  { value: "hours",   label: "Hours"   },
  { value: "days",    label: "Days"    },
  { value: "weeks",   label: "Weeks"   },
  { value: "months",  label: "Months"  },
  { value: "years",   label: "Years"   },
];

const EMPTY = { name: "", download_speed: "", upload_speed: "", duration_value: "", duration_unit: "days", price: "" };

export default function PackageForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isEdit) {
      fetchPackage(id).then((pkg) =>
        setForm({
          name:           pkg.name           || "",
          download_speed: pkg.download_speed || "",
          upload_speed:   pkg.upload_speed   || "",
          duration_value: pkg.duration_value || "",
          duration_unit:  pkg.duration_unit  || "days",
          price:          pkg.price          || "",
        })
      );
    }
  }, [id, isEdit]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (isEdit) await updatePackage(id, form);
      else await createPackage(form);
      navigate("/admin/packages");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to save package");
      setSaving(false);
    }
  };

  return (
    <AdminLayout>
      <div className="max-w-xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">{isEdit ? "Edit Package" : "New Package"}</h1>
          <p className="text-slate-500 text-sm mt-1">
            {isEdit ? "Update the package details below" : "Fill in the details for the new package"}
          </p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-slate-200 p-6 space-y-5">
          <Field label="Package Name" name="name" value={form.name} onChange={handleChange} placeholder="e.g. Basic 5Mbps" required />

          <div className="grid grid-cols-2 gap-4">
            <Field label="Download Speed (Mbps)" type="number" name="download_speed" value={form.download_speed} onChange={handleChange} placeholder="e.g. 5" required />
            <Field label="Upload Speed (Mbps)"   type="number" name="upload_speed"   value={form.upload_speed}   onChange={handleChange} placeholder="e.g. 2" required />
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

          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={saving}
              className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2.5 rounded-lg font-medium text-sm disabled:opacity-50 transition-colors"
            >
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

function Field({ label, name, value, onChange, type = "text", placeholder, required, className = "" }) {
  return (
    <div className={className}>
      <label className="block text-sm font-medium text-slate-700 mb-1.5">{label}</label>
      <input
        type={type}
        name={name}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        required={required}
        className="w-full border border-slate-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
      />
    </div>
  );
}
