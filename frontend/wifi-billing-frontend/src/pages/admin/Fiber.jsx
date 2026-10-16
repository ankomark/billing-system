import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { PageHeader } from "../../components/admin/ui";
import { getUser } from "../../services/auth";
import { fetchFiberOverview, fetchOlts } from "../../services/fiber";
import Overview from "../../components/admin/fiber/Overview";
import { MaintenancePanel, OltPanel, OnuPanel, OutagePanel, ReportPanel, SplitterPanel, TicketPanel } from "../../components/admin/fiber/Panels";
import { Tabs, ghostCls } from "../../components/admin/fiber/shared";

/**
 * Smart Mapping: the fiber plant behind the routers.
 *
 * The tab and the ONU filter live in the URL, so a link to "PON 2, worst
 * signal first" can be sent to a technician and opens on exactly that.
 */
export default function Fiber() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "overview";
  const isAdmin = getUser()?.role !== "tenant_staff";
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["fiber-overview"], queryFn: fetchFiberOverview, refetchInterval: 60 * 1000, staleTime: 30 * 1000,
  });
  const { data: olts = [] } = useQuery({ queryKey: ["fiber-olts"], queryFn: fetchOlts, staleTime: 5 * 60 * 1000 });

  const filter = Object.fromEntries([...params.entries()].filter(([k]) => k !== "tab"));
  const setTab = (id) => setParams({ tab: id });
  const setFilter = (f) => setParams(Object.fromEntries(Object.entries({ tab: "onus", ...f }).filter(([, v]) => v != null && v !== "")));

  return (
    <AdminLayout>
      <div className="space-y-6">
        <PageHeader title="Fiber" subtitle="Every ONU in the field: where it hangs, how far, how strong, and who an outage hits">
          <button className={ghostCls} onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} /> Refresh
          </button>
        </PageHeader>
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "overview", label: "Overview" },
            { id: "onus", label: "ONUs" },
            { id: "outages", label: "Outages", count: data?.open_outages },
            { id: "tickets", label: "Tickets", count: data?.open_tickets },
            { id: "splitters", label: "Splitters" },
            { id: "maintenance", label: "Maintenance" },
            { id: "reports", label: "Reports" },
            { id: "olts", label: "OLTs" },
          ]}
        />
        {tab === "overview" && (data
          ? <Overview data={data} onPort={(olt, port) => setFilter({ olt, port, sort: "signal" })} />
          : <div className="h-40 rounded-xl border border-white/10 bg-slate-900/60 animate-pulse" />)}
        {tab === "onus" && <OnuPanel filter={filter} setFilter={setFilter} />}
        {tab === "outages" && <OutagePanel />}
        {tab === "tickets" && <TicketPanel />}
        {tab === "splitters" && <SplitterPanel isAdmin={isAdmin} />}
        {tab === "maintenance" && <MaintenancePanel olts={olts} />}
        {tab === "reports" && <ReportPanel />}
        {tab === "olts" && <OltPanel isAdmin={isAdmin} />}
      </div>
    </AdminLayout>
  );
}
