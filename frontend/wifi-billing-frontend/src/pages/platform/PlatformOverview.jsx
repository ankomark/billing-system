import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ExternalLink, Plus } from "lucide-react";
import PlatformLayout from "../../components/platform/PlatformLayout";
import OperatorPickerModal from "../../components/platform/OperatorPickerModal";
import AnalyticsPanel from "../../components/platform/AnalyticsPanel";
import {
  PageHeader,
  Section,
  StatTile,
  KES,
  num,
} from "../../components/platform/ui";
import { fetchPlatformOverview } from "../../services/platform";
import { getUser } from "../../services/auth";
import { PLATFORM_OWNER } from "../../constants/roles";

export default function PlatformOverview() {
  const navigate = useNavigate();
  const [picking, setPicking] = useState(false);

  // The backend restricts creating an operator to the platform owner, so
  // platform_staff would get a 403. Hide the button rather than let them find
  // out by filling in the whole form first.
  const isOwner = getUser()?.role === PLATFORM_OWNER;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["platform-overview"],
    queryFn: fetchPlatformOverview,
    staleTime: 60 * 1000,
  });

  if (isError) {
    return (
      <PlatformLayout>
        <p className="text-slate-400">Couldn't load the overview. Try refreshing.</p>
      </PlatformLayout>
    );
  }

  const ops = data?.operators;
  const rev = data?.platform_revenue;
  const net = data?.network;

  return (
    <PlatformLayout>
      <OperatorPickerModal open={picking} onClose={() => setPicking(false)} />

      <div className="space-y-8 max-w-6xl">
        <PageHeader title="Platform overview" subtitle="Every operator on the platform">
          {isOwner && (
            <button
              onClick={() => navigate("/platform/operators/new")}
              className="inline-flex items-center gap-2 bg-teal-500 hover:bg-teal-400 text-slate-950 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
            >
              <Plus size={16} />
              New operator
            </button>
          )}
          <button
            onClick={() => setPicking(true)}
            className="inline-flex items-center gap-2 border border-white/15 hover:bg-white/5 text-slate-200 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
          >
            <ExternalLink size={16} />
            Open operator
          </button>
        </PageHeader>

        {isLoading ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-[92px] rounded-xl border border-white/10 bg-slate-900/60 animate-pulse"
              />
            ))}
          </div>
        ) : (
          <>
            <Section title="Revenue">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile label="Monthly recurring" value={KES(rev.mrr)} />
                <StatTile
                  label="Outstanding"
                  value={KES(rev.outstanding_total)}
                  sub={`${rev.outstanding_count} invoice(s)`}
                  tone={rev.outstanding_count > 0 ? "warning" : "neutral"}
                />
                <StatTile
                  label="Overdue"
                  value={num(rev.overdue_count)}
                  tone={rev.overdue_count > 0 ? "critical" : "neutral"}
                  onClick={
                    rev.overdue_count > 0
                      ? () => navigate("/platform/invoices?overdue=true")
                      : undefined
                  }
                />
              </div>
            </Section>

            <Section title="Operators">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile label="Total" value={num(ops.total)} />
                <StatTile
                  label="Active"
                  value={num(ops.by_status?.active ?? 0)}
                  tone="good"
                />
                <StatTile
                  label="Past due"
                  value={num(ops.by_status?.past_due ?? 0)}
                  tone={(ops.by_status?.past_due ?? 0) > 0 ? "warning" : "neutral"}
                />
                <StatTile
                  label="Restricted"
                  value={num(ops.restricted)}
                  tone={ops.restricted > 0 ? "critical" : "neutral"}
                />
              </div>
            </Section>

            <Section title="Network">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile
                  label="Subscribers"
                  value={num(net.subscribers)}
                  sub={`${num(net.active_subscribers)} active`}
                />
                <StatTile label="Routers" value={num(net.routers)} />
                <StatTile
                  label="Routers offline"
                  value={num(net.routers_offline)}
                  tone={net.routers_offline > 0 ? "critical" : "good"}
                />
              </div>
            </Section>

            <AnalyticsPanel />

            {rev.overdue_count > 0 && (
              <button
                onClick={() => navigate("/platform/invoices?overdue=true")}
                className="w-full flex items-center gap-3 rounded-xl border border-red-500/25 bg-red-500/10 px-5 py-4 text-left text-sm text-red-200 hover:bg-red-500/15 transition-colors"
              >
                <AlertTriangle size={18} className="flex-shrink-0" aria-hidden="true" />
                <span>
                  <strong>{rev.overdue_count}</strong> invoice(s) are past their due
                  date. Review before anyone is restricted.
                </span>
              </button>
            )}
          </>
        )}
      </div>
    </PlatformLayout>
  );
}
