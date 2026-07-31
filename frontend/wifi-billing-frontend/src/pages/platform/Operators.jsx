import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import PlatformLayout from "../../components/platform/PlatformLayout";
import {
  Card,
  DataTable,
  PageHeader,
  StatusBadge,
  STATUS_STYLES,
  KES,
  num,
} from "../../components/platform/ui";
import { fetchOperators } from "../../services/platform";
import { getUser } from "../../services/auth";
import { PLATFORM_OWNER } from "../../constants/roles";

export default function Operators() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  // Creating is owner-only on the backend; see PlatformOverview.
  const isOwner = getUser()?.role === PLATFORM_OWNER;

  const { data: operators = [], isLoading } = useQuery({
    queryKey: ["platform-operators", status],
    queryFn: () => fetchOperators(status || undefined),
    staleTime: 30 * 1000,
  });

  const columns = [
    {
      key: "name",
      label: "Operator",
      render: (op) => (
        <>
          <p className="font-medium text-slate-100">{op.name}</p>
          <p className="text-xs text-slate-500">{op.slug}</p>
        </>
      ),
    },
    {
      key: "status",
      label: "Status",
      render: (op) => <StatusBadge status={op.status} styles={STATUS_STYLES} />,
    },
    { key: "plan", label: "Plan", render: (op) => op.plan || "—" },
    {
      key: "subscribers",
      label: "Subscribers",
      align: "right",
      render: (op) => (
        <>
          {num(op.subscribers)}
          {op.active_subscribers !== op.subscribers && (
            <span className="text-xs text-slate-500"> ({op.active_subscribers} active)</span>
          )}
        </>
      ),
    },
    {
      key: "routers",
      label: "Routers",
      align: "right",
      render: (op) => (
        <>
          <span>{num(op.routers)}</span>
          {op.routers_offline > 0 && (
            <span className="text-red-300 text-xs font-medium">
              {" "}· {op.routers_offline} offline
            </span>
          )}
        </>
      ),
    },
    {
      key: "amount_owed",
      label: "Owed",
      align: "right",
      className: "font-medium text-slate-100",
      render: (op) => (Number(op.amount_owed) > 0 ? KES(op.amount_owed) : "—"),
    },
  ];

  return (
    <PlatformLayout>
      <div className="space-y-6 max-w-6xl">
        <PageHeader
          title="Operators"
          subtitle={`${operators.length} business${operators.length !== 1 ? "es" : ""} on the platform`}
        >
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status"
            className="border border-white/15 bg-slate-900 text-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
          >
            <option value="">All statuses</option>
            <option value="trial">Trial</option>
            <option value="active">Active</option>
            <option value="past_due">Past due</option>
            <option value="restricted">Restricted</option>
            <option value="cancelled">Cancelled</option>
          </select>
          {isOwner && (
            <button
              onClick={() => navigate("/platform/operators/new")}
              className="inline-flex items-center gap-2 bg-teal-500 hover:bg-teal-400 text-slate-950 rounded-lg px-4 py-2 text-sm font-semibold transition-colors whitespace-nowrap"
            >
              <Plus size={16} />
              New operator
            </button>
          )}
        </PageHeader>

        <Card padded={false} className="overflow-hidden">
          {isLoading ? (
            <div className="px-5 py-10 text-center text-sm text-slate-500">
              Loading operators…
            </div>
          ) : (
            <DataTable
              columns={columns}
              rows={operators}
              onRowClick={(op) => navigate(`/platform/operators/${op.id}`)}
              empty="Nobody matches this filter."
            />
          )}
        </Card>
      </div>
    </PlatformLayout>
  );
}
