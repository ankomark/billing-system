// components/usage/AdminUsageGraph.jsx
import { useEffect, useState } from "react";
import { XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Area, ComposedChart } from "recharts";
import { fetchAdminUsageDaily } from "../../services/dashboard";

const DEFAULT_DAYS = 14;
const MB_IN_GB = 1024;

// Simple date formatting without date-fns
function formatDate(dateString) {
  if (!dateString) return '';
  
  try {
    const date = new Date(dateString);
    const month = date.toLocaleString('default', { month: 'short' });
    const day = date.getDate();
    return `${month} ${day}`;
  } catch (e) {
    return dateString;
  }
}

function formatFullDate(dateString) {
  if (!dateString) return '';
  
  try {
    const date = new Date(dateString);
    const month = date.toLocaleString('default', { month: 'short' });
    const day = date.getDate();
    const year = date.getFullYear();
    return `${month} ${day}, ${year}`;
  } catch (e) {
    return dateString;
  }
}

// Inline UsageGraph component
function UsageGraph({ 
  data, 
  height = 350,
  showLegend = true 
}) {
  const formatMBtoGB = (mb) => (mb / 1024).toFixed(1);
  
  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div className="rounded-lg border border-gray-200 bg-white p-3 shadow-lg">
          <p className="font-medium text-gray-900">
            {formatFullDate(label)}
          </p>
          <div className="mt-2 space-y-1">
            <div className="flex items-center justify-between">
              <span className="flex items-center">
                <div className="mr-2 h-3 w-3 rounded-full bg-blue-500" />
                Download
              </span>
              <span className="font-medium">
                {formatMBtoGB(payload[0].value)} GB
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="flex items-center">
                <div className="mr-2 h-3 w-3 rounded-full bg-green-500" />
                Upload
              </span>
              <span className="font-medium">
                {formatMBtoGB(payload[1].value)} GB
              </span>
            </div>
            {payload[2] && (
              <div className="flex items-center justify-between border-t pt-1">
                <span className="font-medium">Total</span>
                <span className="font-bold">
                  {formatMBtoGB(payload[2].value)} GB
                </span>
              </div>
            )}
          </div>
        </div>
      );
    }
    return null;
  };

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <CartesianGrid 
          strokeDasharray="3 3" 
          stroke="#f0f0f0" 
          vertical={false}
        />
        
        <XAxis
          dataKey="day"
          tickFormatter={formatDate}
          stroke="#9ca3af"
          tickLine={false}
          axisLine={false}
          tickMargin={10}
        />
        
        <YAxis
          tickFormatter={(value) => `${(value / 1024).toFixed(0)} GB`}
          stroke="#9ca3af"
          tickLine={false}
          axisLine={false}
          tickMargin={10}
        />
        
        <Tooltip content={<CustomTooltip />} />
        
        {showLegend && (
          <Legend 
            wrapperStyle={{ paddingTop: 20 }}
            formatter={(value) => (
              <span className="text-sm font-medium text-gray-700">{value}</span>
            )}
          />
        )}
        
        <Area
          type="monotone"
          dataKey="total_mb"
          stroke="#8b5cf6"
          fill="#8b5cf6"
          fillOpacity={0.1}
          strokeWidth={2}
          name="Total"
          dot={false}
        />
        
        <Area
          type="monotone"
          dataKey="download_mb"
          stroke="#3b82f6"
          fill="#3b82f6"
          fillOpacity={0.2}
          strokeWidth={2}
          name="Download"
          dot={false}
          activeDot={{ r: 6 }}
        />
        
        <Area
          type="monotone"
          dataKey="upload_mb"
          stroke="#10b981"
          fill="#10b981"
          fillOpacity={0.2}
          strokeWidth={2}
          name="Upload"
          dot={false}
          activeDot={{ r: 6 }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

// Main component
export default function AdminUsageGraph() {
  const [data, setData] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const loadUsageData = async () => {
      try {
        setIsLoading(true);
        setError(null);
        
        const dailyData = await fetchAdminUsageDaily(DEFAULT_DAYS);
        
        const transformedData = dailyData.map(item => ({
          day: item.day,
          download_mb: item.download_gb * MB_IN_GB,
          upload_mb: item.upload_gb * MB_IN_GB,
          total_mb: (item.download_gb + item.upload_gb) * MB_IN_GB,
        }));
        
        setData(transformedData);
      } catch (err) {
        console.error("Failed to fetch admin usage data:", err);
        
        const errorMessage = err.response?.data?.message 
          ? err.response.data.message
          : err.message || "Failed to load usage statistics";
          
        setError(errorMessage);
        setData([]);
      } finally {
        setIsLoading(false);
      }
    };

    loadUsageData();
  }, []);

  // Loading state
  if (isLoading) {
    return (
      <div className="animate-pulse">
        <div className="h-8 w-64 bg-gray-200 rounded mb-4"></div>
        <div className="h-80 bg-gray-200 rounded-xl"></div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <h3 className="font-medium text-red-800">Unable to Load Usage Data</h3>
        <p className="text-sm text-red-600 mt-1">{error}</p>
        <button 
          onClick={() => window.location.reload()}
          className="mt-3 text-sm font-medium text-red-700 hover:text-red-800"
        >
          Try Again
        </button>
      </div>
    );
  }

  // Empty state
  if (data.length === 0) {
    return (
      <div className="text-center py-12">
        <div className="mx-auto h-12 w-12 text-gray-400">
          <svg fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        </div>
        <h3 className="mt-4 text-lg font-medium text-gray-900">No Usage Data Available</h3>
        <p className="mt-2 text-sm text-gray-500">
          There are no usage statistics for the selected period.
        </p>
      </div>
    );
  }

  // Calculate stats
  const totalUsage = data.reduce((total, item) => total + (item.total_mb || 0), 0) / MB_IN_GB;
  const peakDownload = data.length > 0 
    ? Math.max(...data.map(item => item.download_mb || 0)) / MB_IN_GB 
    : 0;
  const peakUpload = data.length > 0 
    ? Math.max(...data.map(item => item.upload_mb || 0)) / MB_IN_GB 
    : 0;

  // Main render
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">
            ISP Total Usage (Last {DEFAULT_DAYS} Days)
          </h3>
          <p className="text-sm text-gray-500">
            Combined download and upload statistics across all customers
          </p>
        </div>
        
        <div className="text-sm text-gray-600">
          Total: {totalUsage.toFixed(1)} GB
        </div>
      </div>
      
      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <UsageGraph data={data} />
      </div>
      
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div className="rounded-lg bg-blue-50 p-3">
          <div className="font-medium text-blue-900">Peak Download</div>
          <div className="text-2xl font-bold text-blue-700">
            {peakDownload.toFixed(1)} GB
          </div>
        </div>
        
        <div className="rounded-lg bg-green-50 p-3">
          <div className="font-medium text-green-900">Peak Upload</div>
          <div className="text-2xl font-bold text-green-700">
            {peakUpload.toFixed(1)} GB
          </div>
        </div>
      </div>
    </div>
  );
}