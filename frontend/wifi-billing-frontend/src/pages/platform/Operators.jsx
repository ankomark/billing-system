import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Building2, Plus } from "lucide-react";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import EmptyState from "../../components/ui/EmptyState";
import { fetchOperators } from "../../services/platform";
import { getUser } from "../../services/auth";
import { PLATFORM_OWNER } from "../../constants/roles";

const STATUS_STYLES = {
  trial:      "bg-blue-50 text-blue-700 border-blue-200",
  active:     "bg-emerald-50 text-emerald-700 border-emerald-200",
  past_due:   "bg-amber-50 text-amber-700 border-amber-200",
  restricted: "bg-red-50 text-red-700 border-red-200",
  cancelled:  "bg-slate-100 text-slate-600 border-slate-200",
};

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

  return (
    <PlatformLayout>
      <div className="space-y-5 max-w-6xl">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Operators</h1>
            <p className="text-slate-500 text-sm mt-0.5">
              {operators.length} business{operators.length !== 1 ? "es" : ""} on the platform
            </p>
          </div>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-teal-500"
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
              className="inline-flex items-center gap-2 bg-teal-600 hover:bg-teal-700 text-white rounded-lg px-4 py-2 text-sm font-semibold transition-colors whitespace-nowrap"
            >
              <Plus size={16} />
              New operator
            </button>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Operator", "Status", "Plan", "Subscribers", "Routers", "Owed"].map((h) => (
                    <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading ? (
                  <SkeletonTable rows={5} cols={6} />
                ) : operators.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState
                        icon={<Building2 size={22} />}
                        title="No operators"
                        description="Nobody matches this filter."
                      />
                    </td>
                  </tr>
                ) : (
                  operators.map((op) => (
                    <tr
                      key={op.id}
                      onClick={() => navigate(`/platform/operators/${op.id}`)}
                      className="hover:bg-slate-50 cursor-pointer transition-colors"
                    >
                      <td className="px-5 py-3.5">
                        <p className="font-medium text-slate-800">{op.name}</p>
                        <p className="text-xs text-slate-400">{op.slug}</p>
                      </td>
                      <td className="px-5 py-3.5">
                        <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold border capitalize ${STATUS_STYLES[op.status] || STATUS_STYLES.cancelled}`}>
                          {op.status.replace("_", " ")}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 text-slate-600">{op.plan || "—"}</td>
                      <td className="px-5 py-3.5 text-slate-700 tabular-nums">
                        {op.subscribers.toLocaleString()}
                        {op.active_subscribers !== op.subscribers && (
                          <span className="text-xs text-slate-400">
                            {" "}({op.active_subscribers} active)
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 tabular-nums">
                        <span className="text-slate-700">{op.routers}</span>
                        {op.routers_offline > 0 && (
                          <span className="text-red-600 text-xs font-medium">
                            {" "}· {op.routers_offline} offline
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 tabular-nums font-medium text-slate-800">
                        {Number(op.amount_owed) > 0
                          ? `KES ${Number(op.amount_owed).toLocaleString()}`
                          : "—"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </PlatformLayout>
  );
}
