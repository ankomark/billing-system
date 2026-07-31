import { useQuery } from "@tanstack/react-query";
import { KeyRound, ShieldCheck, UserCog, UserX, Wallet, Tag, Building2 } from "lucide-react";
import { Card, CardHeader } from "./ui";
import { fetchAuditLog } from "../../services/platform";

/**
 * Who did what to which account.
 *
 * The log has been written since account management landed and was readable
 * nowhere — the same defect this codebase already had with the operator status
 * history: recorded faithfully, surfaced never. An audit trail nobody can read
 * is a cost with no benefit.
 *
 * Reused by the platform page and by the per-operator panel, which is only the
 * same list with a tenant filter.
 */

const ICONS = {
  reset_password: KeyRound,
  change_password: KeyRound,
  change_username: UserCog,
  create_user: UserCog,
  disable_user: UserX,
  enable_user: UserCog,
  change_role: ShieldCheck,
  update_operator: Building2,
  configure_payments: Wallet,
  change_plan: Tag,
};

// The acts worth noticing at a glance. Everything else is routine.
const NOTABLE = new Set(["reset_password", "disable_user", "change_role", "configure_payments"]);

export default function AuditTrail({ tenant, title = "Audit log", limit = 100, compact }) {
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["audit-log", tenant ?? "all", limit],
    queryFn: () => fetchAuditLog({ tenant, limit }),
    staleTime: 30 * 1000,
  });

  const body = isLoading ? (
    <p className="text-sm text-slate-500 py-4">Loading…</p>
  ) : rows.length === 0 ? (
    <p className="text-sm text-slate-500 py-4">
      Nothing has been done to any account yet.
    </p>
  ) : (
    <ul className="divide-y divide-white/5">
      {rows.map((row) => {
        const Icon = ICONS[row.action] || ShieldCheck;
        return (
          <li key={row.id} className="flex items-start gap-3 py-2.5">
            <Icon
              size={14}
              className={`mt-0.5 flex-shrink-0 ${
                NOTABLE.has(row.action) ? "text-amber-300" : "text-slate-500"
              }`}
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-200">
                {row.label}
                {row.target && (
                  <span className="text-slate-400"> · {row.target}</span>
                )}
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                {row.by ? (
                  <>
                    by <span className="text-slate-400">{row.by}</span>
                    {/* Whether this was your own admin or someone from the
                        platform is the first thing an operator wants to know. */}
                    {row.by_platform && (
                      <span className="text-teal-400"> (platform)</span>
                    )}
                  </>
                ) : (
                  "by a deleted account"
                )}
                {!tenant && row.operator && <> · {row.operator}</>}
                {row.detail && <> · {row.detail}</>}
              </p>
            </div>
            <span className="text-xs text-slate-500 whitespace-nowrap">
              {new Date(row.at).toLocaleString("en-KE", {
                month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
              })}
            </span>
          </li>
        );
      })}
    </ul>
  );

  if (compact) return body;

  return (
    <Card>
      <CardHeader
        title={title}
        subtitle="Every change to who can get into an account"
      />
      {body}
    </Card>
  );
}
