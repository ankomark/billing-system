import { useEffect, useState } from "react";
import { fetchPackages, deletePackage } from "../../services/packages";
import { useNavigate } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";

export default function Packages() {
  const [packages, setPackages] = useState([]);
  const navigate = useNavigate();

  const loadPackages = async () => {
    const data = await fetchPackages();
    setPackages(data);
  };

  useEffect(() => {
    loadPackages();
  }, []);

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this package?")) return;
    await deletePackage(id);
    loadPackages();
  };

  return (
    <AdminLayout>
      <div className="p-6">
        <div className="flex justify-between mb-4">
          <h1 className="text-xl font-bold">Packages</h1>

          <button
            onClick={() => navigate("/admin/packages/new")}
            className="bg-blue-600 text-white px-4 py-2 rounded"
          >
            + Add Package
          </button>
        </div>

        <table className="w-full border bg-white">
          <thead className="bg-gray-100">
            <tr>
              <th className="p-2 border">Name</th>
              <th className="p-2 border">Speed</th>
              <th className="p-2 border">Duration</th>
              <th className="p-2 border">Price</th>
              <th className="p-2 border">Actions</th>
            </tr>
          </thead>

          <tbody>
            {packages.map((p) => (
              <tr key={p.id}>
                <td className="p-2 border">{p.name}</td>
                <td className="p-2 border">
                  {p.download_speed}/{p.upload_speed} Mbps
                </td>
                <td className="p-2 border">{p.duration_days} days</td>
                <td className="p-2 border">KES {p.price}</td>
                <td className="p-2 border">
                  <button
                    onClick={() => navigate(`/admin/packages/${p.id}`)}
                    className="text-blue-600 mr-3"
                  >
                    Edit
                  </button>

                  <button
                    onClick={() => handleDelete(p.id)}
                    className="text-red-600"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}

            {packages.length === 0 && (
              <tr>
                <td colSpan="5" className="text-center p-4 text-gray-500">
                  No packages found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </AdminLayout>
  );
}
