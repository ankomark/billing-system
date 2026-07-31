import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, ExternalLink, Plus } from "lucide-react";
import PlatformLayout from "../../components/platform/PlatformLayout";
import OperatorPickerModal from "../../components/platform/OperatorPickerModal";
import { SkeletonCard } from "../../components/ui/Skeleton";
import { fetchPlatformOverview } from "../../services/platform";
import { getUser } from "../../services/auth";
import { PLATFORM_OWNER } from "../../constants/roles";

const KES = (v) => `KES ${Number(v || 0).toLocaleString()}`;

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
        <p className="text-slate-600">Couldn't load the overview. Try refreshing.</p>
      </PlatformLayout>
    );
  }

  const ops = data?.operators;
  const rev = data?.platform_revenue;
  const net = data?.network;

  return (
    <PlatformLayout>
      <OperatorPickerModal open={picking} onClose={() => setPicking(false)} />

      <div className="space-y-6 max-w-5xl">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Platform overview</h1>
            <p className="text-slate-500 text-sm mt-1">
              Every operator on the platform
            </p>
          </div>

          <div className="flex items-center gap-2">
            {isOwner && (
              <button
                onClick={() => navigate("/platform/operators/new")}
                className="inline-flex items-center gap-2 bg-teal-600 hover:bg-teal-700 text-white rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
              >
                <Plus size={16} />
                New operator
              </button>
            )}
            <button
              onClick={() => setPicking(true)}
              className="inline-flex items-center gap-2 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 rounded-lg px-4 py-2 text-sm font-semibold transition-colors"
            >
              <ExternalLink size={16} />
              Open operator
            </button>
          </div>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
          </div>
        ) : (
          <>
            <Section title="Revenue">
              <Stat label="Monthly recurring" value={KES(rev.mrr)} tone="teal" />
              <Stat label="Outstanding" value={KES(rev.outstanding_total)}
                    sub={`${rev.outstanding_count} invoice(s)`} tone="amber" />
              <Stat label="Overdue" value={rev.overdue_count}
                    tone={rev.overdue_count > 0 ? "red" : "slate"} />
            </Section>

            <Section title="Operators">
              <Stat label="Total" value={ops.total} tone="slate" />
              <Stat label="Active" value={ops.by_status?.active ?? 0} tone="teal" />
              <Stat label="Past due" value={ops.by_status?.past_due ?? 0} tone="amber" />
              <Stat label="Restricted" value={ops.restricted}
                    tone={ops.restricted > 0 ? "red" : "slate"} />
            </Section>

            <Section title="Network">
              <Stat label="Subscribers" value={net.subscribers.toLocaleString()}
                    sub={`${net.active_subscribers.toLocaleString()} active`} tone="slate" />
              <Stat label="Routers" value={net.routers} tone="slate" />
              <Stat label="Routers offline" value={net.routers_offline}
                    tone={net.routers_offline > 0 ? "red" : "slate"} />
            </Section>

            {rev.overdue_count > 0 && (
              <button
                onClick={() => navigate("/platform/invoices?overdue=true")}
                className="w-full flex items-center gap-3 bg-red-50 border border-red-200 text-red-800 rounded-xl px-5 py-4 text-left hover:bg-red-100 transition-colors"
              >
                <AlertTriangle size={18} className="flex-shrink-0" />
                <span className="text-sm">
                  <strong>{rev.overdue_count}</strong> invoice(s) are past their
                  due date. Review before anyone is restricted.
                </span>
              </button>
            )}
          </>
        )}
      </div>
    </PlatformLayout>
  );
}

function Section({ title, children }) {
  return (
    <div>
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
        {title}
      </p>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">{children}</div>
    </div>
  );
}

const tones = {
  teal:  "text-teal-700 border-teal-100",
  amber: "text-amber-700 border-amber-100",
  red:   "text-red-700 border-red-100",
  slate: "text-slate-800 border-slate-200",
};

function Stat({ label, value, sub, tone = "slate" }) {
  return (
    <div className={`bg-white rounded-xl border p-4 ${tones[tone]}`}>
      <p className="text-xs text-slate-500 font-medium">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${tones[tone].split(" ")[0]}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}
