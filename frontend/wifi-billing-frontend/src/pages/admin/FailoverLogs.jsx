import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchFailoverLogs } from "../../services/failover";

export default function FailoverLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchFailoverLogs()
      .then(setLogs)
      .finally(() => setLoading(false));
  }, []);

  return (
    <AdminLayout>
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">
          Router Failover Logs
        </h1>

        {loading ? (
          <p>Loading logs…</p>
        ) : logs.length === 0 ? (
          <p className="text-gray-500">No failover events recorded.</p>
        ) : (
          <div className="overflow-x-auto bg-white rounded shadow">
            <table className="w-full text-sm">
              <thead className="bg-gray-100">
                <tr>
                  <th className="p-2 text-left">Customer</th>
                  <th>Phone</th>
                  <th>From Router</th>
                  <th>To Router</th>
                  <th>Reason</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l) => (
                  <tr key={l.id} className="border-t">
                    <td className="p-2">{l.customer}</td>
                    <td>{l.phone}</td>
                    <td>{l.from_router}</td>
                    <td className="font-semibold">{l.to_router}</td>
                    <td>
                      <span
                        className={
                          l.reason === "auto_failover"
                            ? "text-red-600 font-semibold"
                            : "text-blue-600 font-semibold"
                        }
                      >
                        {l.reason}
                      </span>
                    </td>
                    <td>
                      {new Date(l.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AdminLayout>
  );
}
