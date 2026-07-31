import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Tag } from "lucide-react";
import toast from "react-hot-toast";
import { Card, CardHeader, KES } from "./ui";
import { fetchPlatformPlans, setOperatorPlan } from "../../services/platform";

/**
 * Which plan an operator is on, and moving them to another.
 *
 * A plan could only be chosen during onboarding, so an operator who grew out of
 * theirs — or was created without one — could not be moved without going into
 * the database.
 */
export default function OperatorPlanPanel({ operator, canEdit }) {
  const qc = useQueryClient();
  const current = operator.plan;                 // TenantSubscription, or null
  const [choice, setChoice] = useState("");

  const { data: plans = [] } = useQuery({
    queryKey: ["platform-plans"],
    queryFn: fetchPlatformPlans,
    staleTime: 5 * 60 * 1000,
  });

  const move = useMutation({
    mutationFn: (slug) => setOperatorPlan(operator.id, slug),
    onSuccess: (res) => {
      toast.success(res.detail);
      setChoice("");
      qc.invalidateQueries({ queryKey: ["platform-operator", String(operator.id)] });
      qc.invalidateQueries({ queryKey: ["platform-overview"] });
      qc.invalidateQueries({ queryKey: ["audit-log", operator.id, 100] });
    },
    onError: (e) => {
      const data = e.response?.data;
      toast.error(
        (Array.isArray(data?.plan) ? data.plan[0] : data?.detail) ||
          "Couldn't change the plan"
      );
    },
  });

  const offered = plans.filter((p) => p.is_active);

  return (
    <Card>
      <CardHeader
        title="Plan"
        subtitle={
          current
            ? `${current.plan_name} · ${KES(current.plan_price)} per period`
            : "Not on a plan — they are not being billed and have no caps"
        }
      />

      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            aria-label="Move to plan"
            className="rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-2 focus:ring-teal-500"
          >
            <option value="">Move to…</option>
            {offered.map((p) => (
              <option key={p.id} value={p.slug}>
                {p.name} — {KES(p.price)} / {p.billing_period_days} days
              </option>
            ))}
          </select>
          <button
            onClick={() => choice && move.mutate(choice)}
            disabled={!choice || move.isPending}
            className="inline-flex items-center gap-2 rounded-lg bg-teal-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-teal-400 disabled:opacity-50"
          >
            <Tag size={14} aria-hidden="true" />
            {move.isPending ? "Moving…" : "Change plan"}
          </button>
          <span className="text-xs text-slate-500">
            {/* Re-dating the period would either bill them twice for the same
                days or hand them a free one, depending which way they moved. */}
            Takes effect from their next invoice. The current period is unchanged.
          </span>
        </div>
      ) : (
        <p className="text-xs text-slate-500">
          Only the platform owner can change what an operator is billed.
        </p>
      )}
    </Card>
  );
}
