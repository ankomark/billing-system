import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchCustomers, deleteCustomer } from "../../services/customers";

export default function Customers() {
  const [customers, setCustomers] = useState([]);

  const loadCustomers = async () => {
    const data = await fetchCustomers();
    setCustomers(data);
  };

  useEffect(() => {
    loadCustomers();
  }, []);

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this customer?")) return;
    await deleteCustomer(id);
    loadCustomers();
  };

  return (
    <AdminLayout>
      <div className="p-6">
        <h1 className="text-xl font-bold mb-4">Customers</h1>

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
            {customers.map((c) => (
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
            ))}

            {customers.length === 0 && (
              <tr>
                <td colSpan="5" className="text-center p-4 text-gray-500">
                  No customers found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </AdminLayout>
  );
}
