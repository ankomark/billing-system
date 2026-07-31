import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { Skeleton } from "../../components/ui/Skeleton";
import { fetchMyPlatformAccount } from "../../services/platform";

const KES = (v) => `KES ${Number(v || 0).toLocaleString()}`;

/**
 * What this operator owes the platform.
 *
 * Deliberately reachable even while their account is restricted — it is the
 * page that tells them why, and locking them out of it would leave no way back.
 */
export default function MyPlatformAccount() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["my-platform-account"],
    queryFn: fetchMyPlatformAccount,
    staleTime: 60 * 1000,
  });

  if (isLoading) {
    return (
      <AdminLayout>
        <div className="max-w-2xl space-y-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-32 w-full" />
        </div>
      </AdminLayout>
    );
  }

  if (isError) {
    return (
      <AdminLayout>
        <p className="text-slate-500">Couldn't load your account.</p>
      </AdminLayout>
    );
  }

  // `cancelled` counts as restricted on the backend — Tenant.is_restricted
  // returns true for both — but this page only knew about "restricted", so a
  // cancelled operator saw a locked dashboard and no explanation anywhere.
  const cancelled = data.account_status === "cancelled";
  const restricted = data.account_status === "restricted" || cancelled;
  const pastDue = data.account_status === "past_due";

  return (
    <AdminLayout>
      <div className="space-y-6 max-w-2xl">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Your subscription</h1>
          <p className="text-slate-500 text-sm mt-1">{data.operator}</p>
        </div>

        {(restricted || pastDue) && (
          <div className={`rounded-xl border px-5 py-4 flex gap-3 ${
            restricted
              ? "bg-red-50 border-red-200 text-red-800"
              : "bg-amber-50 border-amber-200 text-amber-800"
          }`}>
            <AlertTriangle size={18} className="flex-shrink-0 mt-0.5" />
            <div className="text-sm">
              <p className="font-semibold">
                {cancelled
                  ? "Your account has been closed"
                  : restricted
                  ? "Your dashboard is locked"
                  : "Payment overdue"}
              </p>
              <p className="mt-1">
                {cancelled
                  ? "Contact the platform to discuss reopening it. Your customers are not affected — they keep their internet and can still renew."
                  : restricted
                  ? "Settle the invoice below to restore access. Your customers are not affected — they keep their internet and can still renew."
                  : "Settle the invoice below to avoid your dashboard being locked. Your customers are never affected."}
              </p>
            </div>
          </div>
        )}

        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">
            Plan
          </h2>
          {data.subscription ? (
            <div className="grid grid-cols-2 gap-4 text-sm">
              <Row label="Plan" value={data.subscription.plan_name} />
              <Row label="Price" value={KES(data.subscription.plan_price)} />
              <Row label="Status" value={data.subscription.status} />
              <Row
                label="Renews"
                value={new Date(data.subscription.current_period_end).toLocaleDateString("en-KE")}
              />
            </div>
          ) : (
            <p className="text-sm text-slate-400">
              You are not on a plan yet. Contact the platform to get set up.
            </p>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
              Outstanding
            </h2>
            <span className="text-lg font-bold text-slate-800">
              {KES(data.amount_due)}
            </span>
          </div>

          {data.outstanding.length === 0 ? (
            <p className="text-sm text-emerald-600 font-medium">
              Nothing outstanding. You're all paid up.
            </p>
          ) : (
            <ul className="space-y-2">
              {data.outstanding.map((inv) => (
                <li key={inv.id} className="flex justify-between items-center text-sm border-t border-slate-100 pt-2.5">
                  <div>
                    <code className="text-xs bg-slate-100 px-2 py-0.5 rounded">{inv.number}</code>
                    <p className="text-xs text-slate-400 mt-1">
                      Due {new Date(inv.due_date).toLocaleDateString("en-KE")}
                    </p>
                  </div>
                  <span className={`font-semibold ${inv.is_overdue ? "text-red-600" : "text-slate-800"}`}>
                    {KES(inv.amount)}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <p className="text-xs text-slate-400 mt-4">
            Payments are recorded by the platform once received. Contact them
            with your M-Pesa reference if a payment isn't showing.
          </p>
        </div>
      </div>
    </AdminLayout>
  );
}

function Row({ label, value }) {
  return (
    <div>
      <p className="text-slate-400 text-xs">{label}</p>
      <p className="font-medium text-slate-800 capitalize">{value}</p>
    </div>
  );
}
