import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

export default function UsageGraph({ data, title }) {
  if (!data || data.length === 0) {
    return (
      <div className="bg-white p-4 rounded shadow mt-6">
        <h3 className="font-semibold mb-3">{title}</h3>
        <p className="text-sm text-gray-500">No usage data available</p>
      </div>
    );
  }

  return (
    <div className="bg-white p-4 rounded shadow mt-6">
      <h3 className="font-semibold mb-3">{title}</h3>

      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="day" />
          <YAxis />
          <Tooltip />
          <Line
            type="monotone"
            dataKey="download_mb"
            stroke="#2563eb"
            name="Download (MB)"
          />
          <Line
            type="monotone"
            dataKey="upload_mb"
            stroke="#16a34a"
            name="Upload (MB)"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
