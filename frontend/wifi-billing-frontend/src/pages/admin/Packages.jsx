import { useEffect, useState } from "react";
import { fetchPackages, deletePackage } from "../../services/packages";
import { useNavigate } from "react-router-dom";
import AdminLayout from "../../components/admin/AdminLayout";

export default function Packages() {
  const [packages, setPackages] = useState([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [count, setCount] = useState(0);
  const navigate = useNavigate();

  const loadPackages = async (p = page) => {
    const data = await fetchPackages(p);
    setPackages(data.results);
    setTotalPages(data.total_pages);
    setCount(data.count);
  };

  useEffect(() => {
    loadPackages(page);
  }, [page]);

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this package?")) return;
    await deletePackage(id);
    loadPackages(page);
  };

  return (
    <AdminLayout>
      <div className="p-6">
        <div className="flex justify-between mb-4">
          <h1 className="text-xl font-bold">
            Packages{count > 0 && <span className="text-sm text-gray-500 font-normal ml-2">({count} total)</span>}
          </h1>

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
                <td className="p-2 border">
                  {p.duration_value} {p.duration_unit}
                </td>
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

        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-4">
            <button
              onClick={() => setPage((p) => Math.max(p - 1, 1))}
              disabled={page <= 1}
              className="px-4 py-2 border rounded disabled:opacity-40 hover:bg-gray-100"
            >
              ← Previous
            </button>

            <span className="text-sm text-gray-600">
              Page {page} of {totalPages}
            </span>

            <button
              onClick={() => setPage((p) => Math.min(p + 1, totalPages))}
              disabled={page >= totalPages}
              className="px-4 py-2 border rounded disabled:opacity-40 hover:bg-gray-100"
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
