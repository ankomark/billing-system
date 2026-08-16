import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Activity, CreditCard, Clock, RefreshCw, Users, Wallet,
} from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import AdminUsageGraph from "../../components/usage/AdminUsageGraph";
import AnalyticsPanels from "../../components/admin/AnalyticsPanels";
import CustomerQuickSearch from "../../components/admin/CustomerQuickSearch";
import IssueVoucherCard from "../../components/admin/IssueVoucherCard";
import {
  Card, CardHeader, Note, PageHeader, PulseBanner, Section, StatTile, KES, num,
} from "../../components/admin/ui";
import { fetchDashboardSummary } from "../../services/dashboard";

/**
 * The operator's own overview.
 *
 * Colour does two jobs here, and they are kept apart.
 *
 * A VALUE takes colour only from state: emerald when a number is healthy, red
 * when it needs doing something about. That rule is why the stat cards stopped
 * being blue, emerald, violet, amber and red by position — "This year" was
 * violet, which says nothing, while "Unpaid invoices" got no more emphasis than
 * anything else. Money figures still carry no status colour, because there is
 * no threshold at which revenue is wrong.
 *
 * A CHIP takes colour from identity: which metric this is. It paints the icon
 * square and never the number, so it cannot be mistaken for a verdict, and it
 * is tied to the metric rather than to the position — a tile keeps its hue when
 * its neighbour is removed. Those hues come from the same CVD-validated set the
 * charts draw from, and each was measured to carry a white icon.
 *
 * The headline revenue sits in a hero band rather than three more tiles: it is
 * what the page is opened for, and it used to be the same size as the count of
 * pending invoices.
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
    <div className="h-[92px] rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 animate-pulse" />
  );

  return (
    <AdminLayout>
      <div className="space-y-8 max-w-6xl">
        <PageHeader title="Dashboard" subtitle="How your business is doing today">
          {dataUpdatedAt > 0 && (
            <span className="hidden sm:block text-xs text-slate-500">
              Updated {new Date(dataUpdatedAt).toLocaleTimeString("en-KE")}
            </span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-white/5 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </PageHeader>

        {/* The three figures an operator opens this page for, in the hero form
            rather than as three more tiles competing with the counts below. */}
        <PulseBanner
          title="Money in"
          loading={isLoading}
          items={[
            { label: "Today", value: KES(rev?.today) },
            { label: "This month", value: KES(rev?.this_month) },
            { label: "This year", value: KES(rev?.this_year) },
          ]}
        />

        <Section title="Your customers">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {isLoading ? (
              <>{skeleton}{skeleton}{skeleton}{skeleton}</>
            ) : (
              <>
                {/* Each chip names its metric, so a tile keeps its colour when
                    it drops to a neutral tone. Where a tone is set the status
                    colour takes the chip instead — see StatTile. */}
                <StatTile
                  label="Active"
                  value={num(stats?.active_subscriptions)}
                  chip="aqua"
                  icon={Users}
                  glass="azure"
                  tone="good"
                />
                <StatTile
                  label="Expired"
                  value={num(stats?.expired_subscriptions)}
                  sub="not paying"
                  chip="orange"
                  icon={Clock}
                  glass="ruby"
                  tone={stats?.expired_subscriptions > 0 ? "warning" : "neutral"}
                  onClick={() => navigate("/admin/customers")}
                />
                <StatTile
                  label="Unpaid invoices"
                  value={num(stats?.unpaid_invoices)}
                  sub="money owed to you"
                  chip="magenta"
                  icon={CreditCard}
                  glass="pearl"
                  tone={stats?.unpaid_invoices > 0 ? "critical" : "neutral"}
                  onClick={() => navigate("/admin/invoices/unpaid")}
                />
                <StatTile
                  label="Pending"
                  value={num(stats?.pending_invoices)}
                  sub="awaiting M-Pesa"
                  chip="violet"
                  icon={Wallet}
                  glass="azure"
                  tone={stats?.pending_invoices > 0 ? "info" : "neutral"}
                />
              </>
            )}
          </div>
        </Section>

        {/* Selling comes before looking anything up: it is the transaction,
            not a report about transactions. */}
        <IssueVoucherCard />

        {/* Above the charts on purpose. Looking one person up is the thing an
            operator does most, and it used to mean leaving this page for the
            customer list and searching again there. */}
        <CustomerQuickSearch />

        <Card>
          <CardHeader
            title="Network usage"
            subtitle="Across every router, last 7 days"
            chip="blue"
            icon={Activity}
          />
          <AdminUsageGraph />
        </Card>

        {/* The same panels as the Analytics page, in compact form — the pulse
            and totals tiles are dropped because the figures above already say
            those, and the same number twice on one screen makes a reader stop
            to check whether they disagree. */}
        <AnalyticsPanels compact defaultDays={30} />
      </div>
    </AdminLayout>
  );
}
