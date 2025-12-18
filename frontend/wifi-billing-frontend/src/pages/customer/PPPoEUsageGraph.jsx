import { useEffect, useState } from "react";
import { fetchPPPoEUsageDaily } from "../../services/pppoe";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export default function PPPoEUsageGraph() {
  const [rows, setRows] = useState([]);

  useEffect(() => {
    fetchPPPoEUsageDaily(7).then(setRows);
  }, []);

  return (
    <div className="bg-white shadow rounded p-4 mt-6">
      <h3 className="font-semibold mb-3">Usage (Last 7 Days)</h3>

      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="day" />
            <YAxis />
            <Tooltip />
            <Line type="monotone" dataKey="download_mb" />
            <Line type="monotone" dataKey="upload_mb" />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <p className="text-xs text-gray-500 mt-2">
        Download/Upload shown in MB per day.
      </p>
    </div>
  );
}
