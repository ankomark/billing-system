import { useEffect, useState } from "react";
import { fetchRouters } from "../../services/routers";

export default function Routers() {
  const [routers, setRouters] = useState([]);

  useEffect(() => {
    fetchRouters().then(setRouters);
  }, []);

  return (
    <div className="p-6 space-y-6 bg-orange-300" >
      {/* Page Header */}
      <div>
        <h1 className="text-2xl font-semibold text-gray-800">Routers</h1>
        <p className="text-sm text-gray-50 mt-1">
          Monitor router availability, priority, and network status
        </p>
      </div>

      {/* Table Card */}
      <div className="bg-teal-500 rounded-xl shadow-sm border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm text-gray-700">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="px-6 py-4 text-left font-semibold">Name</th>
              <th className="px-6 py-4 text-left font-semibold">IP Address</th>
              <th className="px-6 py-4 text-left font-semibold">Priority</th>
              <th className="px-6 py-4 text-left font-semibold">Status</th>
            </tr>
          </thead>

          <tbody>
            {routers.length === 0 && (
              <tr>
                <td
                  colSpan="4"
                  className="px-6 py-10 text-center text-gray-500"
                >
                  No routers found
                </td>
              </tr>
            )}

            {routers.map((router) => (
              <tr
                key={router.id}
                className="border-b last:border-none hover:bg-gray-50 transition"
              >
                <td className="px-6 py-4 font-medium text-gray-800">
                  {router.name}
                </td>

                <td className="px-6 py-4 font-mono text-xs">
                  {router.ip_address}
                </td>

                <td className="px-6 py-4">
                  <span className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-blue-50 text-blue-700">
                    Priority {router.priority}
                  </span>
                </td>

                <td className="px-6 py-4">
                  {router.online ? (
                    <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-700">
                      ● ONLINE
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-red-100 text-red-700">
                      ● OFFLINE
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
