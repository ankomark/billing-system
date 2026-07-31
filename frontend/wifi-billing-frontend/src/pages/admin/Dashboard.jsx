import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import AdminUsageGraph from "../../components/usage/AdminUsageGraph";
import {
  Card, CardHeader, Note, PageHeader, Section, StatTile, KES, num,
} from "../../components/admin/ui";
import { fetchDashboardSummary } from "../../services/dashboard";

/**
 * The operator's own overview.
 *
 * Colour here means state, not decoration. The stat cards used to be blue,
 * emerald, violet, amber and red assigned by position — "This Year" was violet,
 * which says nothing, while "Unpaid invoices" got no more emphasis than
 * anything else. Money figures are now neutral, and only the numbers that mean
 * something needs doing take a status colour.
 */
export default function Dashboard() {
  const navigate = useNavigate();

  const { data, isLoading, isError, isFetching, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: fetchDashboardSummary,
    staleTime: 60 * 1000,
  });

  if (isError) {
    return (
      <AdminLayout>
        <div className="max-w-2xl space-y-4">
          <PageHeader title="Dashboard" />
          <Note tone="critical" title="Couldn't load your dashboard">
            <p>Check your connection and try again.</p>
            <button
              onClick={() => refetch()}
              className="mt-3 inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 transition-colors"
            >
              <RefreshCw size={14} /> Retry
            </button>
          </Note>
        </div>
      </AdminLayout>
    );
  }

  const rev = data?.revenue_summary;
  const stats = data?.customer_stats;
  const skeleton = (
    <div className="h-[92px] rounded-xl border border-slate-200 bg-white shadow-sm animate-pulse" />
  );

  return (
    <AdminLayout>
      <div className="space-y-8 max-w-6xl">
        <PageHeader title="Dashboard" subtitle="How your business is doing today">
          {dataUpdatedAt > 0 && (
            <span className="hidden sm:block text-xs text-slate-400">
              Updated {new Date(dataUpdatedAt).toLocaleTimeString("en-KE")}
            </span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </PageHeader>

        <Section title="Money in">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {isLoading ? (
              <>{skeleton}{skeleton}{skeleton}</>
            ) : (
              <>
                <StatTile label="Today" value={KES(rev?.today)} />
                <StatTile label="This month" value={KES(rev?.this_month)} />
                <StatTile label="This year" value={KES(rev?.this_year)} />
              </>
            )}
          </div>
        </Section>

        <Section title="Your customers">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {isLoading ? (
              <>{skeleton}{skeleton}{skeleton}{skeleton}</>
            ) : (
              <>
                <StatTile
                  label="Active"
                  value={num(stats?.active_subscriptions)}
                  tone="good"
                />
                <StatTile
                  label="Expired"
                  value={num(stats?.expired_subscriptions)}
                  sub="not paying"
                  tone={stats?.expired_subscriptions > 0 ? "warning" : "neutral"}
                  onClick={() => navigate("/admin/customers")}
                />
                <StatTile
                  label="Unpaid invoices"
                  value={num(stats?.unpaid_invoices)}
                  sub="money owed to you"
                  tone={stats?.unpaid_invoices > 0 ? "critical" : "neutral"}
                  onClick={() => navigate("/admin/invoices/unpaid")}
                />
                <StatTile
                  label="Pending"
                  value={num(stats?.pending_invoices)}
                  sub="awaiting M-Pesa"
                  tone={stats?.pending_invoices > 0 ? "info" : "neutral"}
                />
              </>
            )}
          </div>
        </Section>

        <Card>
          <CardHeader
            title="Network usage"
            subtitle="Across every router, last 7 days"
          />
          <AdminUsageGraph />
        </Card>
      </div>
    </AdminLayout>
  );
}
