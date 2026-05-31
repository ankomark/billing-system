import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchCustomers, deleteCustomer } from "../../services/customers";

export default function Customers() {
  const [customers, setCustomers] = useState([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(false);

  const PAGE_SIZE = 25;

  const loadCustomers = async (p = page) => {
    setLoading(true);
    try {
      const data = await fetchCustomers(p, PAGE_SIZE);
      setCustomers(data.results);
      setTotalPages(data.total_pages);
      setCount(data.count);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCustomers(page);
  }, [page]);

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this customer?")) return;
    await deleteCustomer(id);
    loadCustomers(page);
  };

  const start = (page - 1) * PAGE_SIZE + 1;
  const end = Math.min(page * PAGE_SIZE, count);

  return (
    <AdminLayout>
      <div className="p-6">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-xl font-bold">Customers</h1>
          {count > 0 && (
            <span className="text-sm text-gray-500">
              Showing {start}–{end} of {count}
            </span>
          )}
        </div>

        <table className="w-full bg-white border">
          <thead className="bg-gray-200">
            <tr>
              <th className="p-2 border">Name</th>
              <th className="p-2 border">Phone</th>
              <th className="p-2 border">Type</th>
              <th className="p-2 border">Status</th>
              <th className="p-2 border">Actions</th>
            </tr>
          </thead>

          <tbody>
            {loading ? (
              <tr>
                <td colSpan="5" className="text-center p-4 text-gray-400">
                  Loading...
                </td>
              </tr>
            ) : customers.length === 0 ? (
              <tr>
                <td colSpan="5" className="text-center p-4 text-gray-500">
                  No customers found
                </td>
              </tr>
            ) : (
              customers.map((c) => (
                <tr key={c.id}>
                  <td className="p-2 border">{c.full_name}</td>
                  <td className="p-2 border">{c.phone}</td>
                  <td className="p-2 border">{c.connection_type}</td>
                  <td className="p-2 border">{c.status}</td>
                  <td className="p-2 border">
                    <button
                      onClick={() => handleDelete(c.id)}
                      className="text-red-600"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))
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
