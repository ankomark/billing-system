// import { useEffect, useState } from "react";
// import { useNavigate, useParams } from "react-router-dom";
// import AdminLayout from "../../components/admin/AdminLayout";
// import {
//   fetchPackage,
//   createPackage,
//   updatePackage,
// } from "../../services/packages";

// export default function PackageForm() {
//   const { id } = useParams();
//   const navigate = useNavigate();
//   const isEdit = Boolean(id);

//   const [form, setForm] = useState({
//     name: "",
//     download_speed: "",
//     upload_speed: "",
//     duration_days: "",
//     price: "",
//   });

//   useEffect(() => {
//     if (isEdit) {
//       fetchPackage(id).then((pkg) => {
//         setForm({
//           name: pkg.name || "",
//           download_speed: pkg.download_speed || "",
//           upload_speed: pkg.upload_speed || "",
//           duration_days: pkg.duration_days || "",
//           price: pkg.price || "",
//         });
//       });
//     }
//   }, [id, isEdit]);

//   const handleChange = (e) => {
//     const { name, value } = e.target;
//     setForm((prev) => ({ ...prev, [name]: value }));
//   };

//   const handleSubmit = async (e) => {
//     e.preventDefault();

//     if (isEdit) {
//       await updatePackage(id, form);
//     } else {
//       await createPackage(form);
//     }

//     navigate("/admin/packages");
//   };

//   return (
//     <AdminLayout>
//       <div className="p-6 max-w-xl">
//         <h1 className="text-xl font-bold mb-4">
//           {isEdit ? "Edit Package" : "Add Package"}
//         </h1>

//         <form
//           onSubmit={handleSubmit}
//           className="space-y-4 bg-white p-6 shadow rounded"
//         >
//           <input
//             name="name"
//             value={form.name}
//             onChange={handleChange}
//             placeholder="Package Name"
//             className="input"
//             required
//           />

//           <input
//             type="number"
//             name="download_speed"
//             value={form.download_speed}
//             onChange={handleChange}
//             placeholder="Download Speed (Mbps)"
//             className="input"
//             required
//           />

//           <input
//             type="number"
//             name="upload_speed"
//             value={form.upload_speed}
//             onChange={handleChange}
//             placeholder="Upload Speed (Mbps)"
//             className="input"
//             required
//           />

//           <input
//             type="number"
//             name="duration_days"
//             value={form.duration_days}
//             onChange={handleChange}
//             placeholder="Duration (Days)"
//             className="input"
//             required
//           />

//           <input
//             type="number"
//             name="price"
//             value={form.price}
//             onChange={handleChange}
//             placeholder="Price (KES)"
//             className="input"
//             required
//           />

//           <button
//             type="submit"
//             className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded"
//           >
//             Save Package
//           </button>
//         </form>
//       </div>
//     </AdminLayout>
//   );
// }
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";
import {
  fetchPackage,
  createPackage,
  updatePackage,
} from "../../services/packages";

const DURATION_UNITS = [
  { value: "minutes", label: "Minutes" },
  { value: "hours", label: "Hours" },
  { value: "days", label: "Days" },
  { value: "weeks", label: "Weeks" },
  { value: "months", label: "Months" },
  { value: "years", label: "Years" },
];

export default function PackageForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [form, setForm] = useState({
    name: "",
    download_speed: "",
    upload_speed: "",
    duration_value: "",
    duration_unit: "days",
    price: "",
  });

  useEffect(() => {
    if (isEdit) {
      fetchPackage(id).then((pkg) => {
        setForm({
          name: pkg.name || "",
          download_speed: pkg.download_speed || "",
          upload_speed: pkg.upload_speed || "",
          duration_value: pkg.duration_value || "",
          duration_unit: pkg.duration_unit || "days",
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

          {/* ✅ Duration value */}
          <input
            type="number"
            name="duration_value"
            value={form.duration_value}
            onChange={handleChange}
            placeholder="Duration value (e.g. 30, 12, 1)"
            className="input"
            required
          />

          {/* ✅ Duration unit */}
          <select
            name="duration_unit"
            value={form.duration_unit}
            onChange={handleChange}
            className="input"
            required
          >
            {DURATION_UNITS.map((u) => (
              <option key={u.value} value={u.value}>
                {u.label}
              </option>
            ))}
          </select>

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
