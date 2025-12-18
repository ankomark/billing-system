import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import { fetchDashboardSummary } from "../../services/dashboard";
import AdminUsageGraph from "../../components/usage/AdminUsageGraph";

export default function Dashboard() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetchDashboardSummary().then(setData);
  }, []);

  if (!data) {
    return (
      <AdminLayout>
        <p className="p-6">Loading dashboard...</p>
      </AdminLayout>
    );
  }

  const { revenue_summary, customer_stats } = data;

  return (
    <AdminLayout>
      <div className="p-6 bg-sky-200 rounded-md">
        <h1 className="text-2xl font-bold mb-6">
          Skylink Admin Dashboard
        </h1>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <Card title="Today Revenue" value={`KES ${revenue_summary.today}`} />
          <Card title="This Month" value={`KES ${revenue_summary.this_month}`} />
          <Card title="This Year" value={`KES ${revenue_summary.this_year}`} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
          <Card title="Active Subs" value={customer_stats.active_subscriptions} />
          <Card title="Expired Subs" value={customer_stats.expired_subscriptions} />
          <Card title="Unpaid Invoices" value={customer_stats.unpaid_invoices} />
          <Card title="Pending Invoices" value={customer_stats.pending_invoices} />
        </div>
        <AdminUsageGraph />
      </div>
    </AdminLayout>
    
  );
}

function Card({ title, value }) {
  return (
    <div className="bg-white rounded shadow p-6">
      <h3 className="text-gray-500 text-sm">{title}</h3>
      <p className="text-2xl font-bold mt-2">{value}</p>
    </div>
  );
}
