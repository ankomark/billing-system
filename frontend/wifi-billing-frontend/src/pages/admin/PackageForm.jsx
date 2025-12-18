import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";
import {
  fetchPackage,
  createPackage,
  updatePackage,
} from "../../services/packages";

export default function PackageForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState({
    name: "",
    download_speed: "",
    upload_speed: "",
    duration_days: "",
    price: "",
  });

  useEffect(() => {
    if (isEdit) {
      fetchPackage(id).then((pkg) => {
        setForm({
          name: pkg.name || "",
          download_speed: pkg.download_speed || "",
          upload_speed: pkg.upload_speed || "",
          duration_days: pkg.duration_days || "",
          price: pkg.price || "",
        });
      });
    }
  }, [id, isEdit]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (isEdit) {
      await updatePackage(id, form);
    } else {
      await createPackage(form);
    }

    navigate("/admin/packages");
  };

  return (
    <AdminLayout>
      <div className="p-6 max-w-xl">
        <h1 className="text-xl font-bold mb-4">
          {isEdit ? "Edit Package" : "Add Package"}
        </h1>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 bg-white p-6 shadow rounded"
        >
          <input
            name="name"
            value={form.name}
            onChange={handleChange}
            placeholder="Package Name"
            className="input"
            required
          />

          <input
            type="number"
            name="download_speed"
            value={form.download_speed}
            onChange={handleChange}
            placeholder="Download Speed (Mbps)"
            className="input"
            required
          />

          <input
            type="number"
            name="upload_speed"
            value={form.upload_speed}
            onChange={handleChange}
            placeholder="Upload Speed (Mbps)"
            className="input"
            required
          />

          <input
            type="number"
            name="duration_days"
            value={form.duration_days}
            onChange={handleChange}
            placeholder="Duration (Days)"
            className="input"
            required
          />

          <input
            type="number"
            name="price"
            value={form.price}
            onChange={handleChange}
            placeholder="Price (KES)"
            className="input"
            required
          />

          <button
            type="submit"
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded"
          >
            Save Package
          </button>
        </form>
      </div>
    </AdminLayout>
  );
}
