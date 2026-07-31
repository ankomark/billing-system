import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowLeft, Eye, ShieldAlert, ShieldCheck } from "lucide-react";
import PlatformLayout from "../../components/platform/PlatformLayout";
import { Skeleton } from "../../components/ui/Skeleton";
import { startImpersonating } from "../../services/auth";
import { fetchOperator, setOperatorStatus } from "../../services/platform";
import { KES } from "../../components/platform/ui";

export default function OperatorDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: op, isLoading } = useQuery({
    queryKey: ["platform-operator", id],
    queryFn: () => fetchOperator(id),
  });

  if (isLoading) {
    return (
      <PlatformLayout>
        <div className="max-w-4xl space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-40 w-full" />
        </div>
      </PlatformLayout>
    );
  }

  const changeStatus = async (status) => {
    if (!reason.trim() && (status === "restricted" || status === "cancelled")) {
      toast.error("A reason is required — it is what answers a dispute later.");
      return;
    }
    setBusy(true);
    try {
      await setOperatorStatus(id, { status, reason: reason.trim() });
      toast.success(status === "active" ? "Access restored" : "Operator restricted");
      setReason("");
      qc.invalidateQueries({ queryKey: ["platform-operator", id] });
    } catch (e) {
      toast.error(e.response?.data?.detail || "Couldn't change status");
    } finally {
      setBusy(false);
    }
  };

  const viewAs = () => {
    if (!reason.trim()) {
      toast.error("Say why you're viewing this account — it goes in the audit log.");
      return;
    }
    startImpersonating({ id: op.id, name: op.name, reason: reason.trim() });
    navigate("/admin/dashboard");
  };

  return (
    <PlatformLayout>
      <div className="space-y-6 max-w-4xl">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate("/platform/operators")}
                  className="text-slate-500 hover:text-white transition-colors">
            <ArrowLeft size={20} />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-white truncate">{op.name}</h1>
            <p className="text-slate-400 text-sm mt-0.5 capitalize">
              {op.status.replace("_", " ")}
              {op.plan ? ` · ${op.plan.plan_name}` : " · no plan"}
            </p>
          </div>
        </div>

        {!op.payments_configured && (
          <div className="border border-amber-500/25 bg-amber-500/10 text-amber-200 rounded-xl px-5 py-3 text-sm">
            This operator hasn't finished M-Pesa setup, so they cannot take any
            money yet. Usually the reason a new operator appears stuck.
          </div>
        )}

        <div className="grid sm:grid-cols-3 gap-4">
          <Card label="Subscribers" value={op.network.subscribers.toLocaleString()} />
          <Card label="Routers" value={op.network.routers} />
          <Card label="Their revenue" value={KES(op.network.subscriber_revenue)} />
        </div>

        <Panel title="Owes the platform">
          <div className="grid sm:grid-cols-2 gap-4 text-sm">
            <Row label="Outstanding" value={KES(op.billing.amount_owed)} />
            <Row label="Paid to date" value={KES(op.billing.lifetime_paid)} />
          </div>
          {op.billing.outstanding.length > 0 && (
            <ul className="mt-4 space-y-1.5">
              {op.billing.outstanding.map((inv) => (
                <li key={inv.id} className="flex justify-between text-sm border-t border-white/10 pt-2">
                  <code className="text-xs bg-white/5 border border-white/10 px-2 py-0.5 rounded text-slate-300">{inv.number}</code>
                  <span className={inv.is_overdue ? "text-red-300 font-medium" : "text-slate-300"}>
                    {KES(inv.amount)}{inv.is_overdue ? " · overdue" : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Support actions">
          <p className="text-sm text-slate-400 mb-3">
            A reason is required. It is recorded against your account and shown
            to whoever reviews this operator later.
          </p>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Ticket 481 — investigating a failed payment"
            className="w-full border border-white/15 bg-slate-950 text-slate-100 placeholder-slate-600 rounded-lg px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-teal-500"
          />
          <div className="flex flex-wrap gap-2">
            <button onClick={viewAs} disabled={busy}
                    className="inline-flex items-center gap-2 bg-teal-500 hover:bg-teal-400 text-slate-950 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50">
              <Eye size={14} /> View as this operator
            </button>
            {op.is_restricted ? (
              <button onClick={() => changeStatus("active")} disabled={busy}
                      className="inline-flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50">
                <ShieldCheck size={14} /> Restore access
              </button>
            ) : (
              <button onClick={() => changeStatus("restricted")} disabled={busy}
                      className="inline-flex items-center gap-2 bg-red-500 hover:bg-red-400 text-slate-950 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50">
                <ShieldAlert size={14} /> Restrict
              </button>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-3">
            Restricting locks this operator out of their dashboard and stops new
            signups. Their existing customers keep their internet and can still
            renew.
          </p>
        </Panel>

        <Panel title="Recent support access">
          {op.recent_support_access.length === 0 ? (
            <p className="text-sm text-slate-500">Nobody has viewed this operator.</p>
          ) : (
            <ul className="space-y-2">
              {op.recent_support_access.map((log, i) => (
                <li key={i} className="text-sm flex justify-between gap-4 border-b border-white/10 pb-2 last:border-0">
                  <span className="text-slate-300">
                    <strong>{log.by}</strong>{" "}
                    <code className="text-xs text-slate-500">{log.method} {log.path}</code>
                    {log.reason && <span className="text-slate-500"> — {log.reason}</span>}
                  </span>
                  <span className="text-slate-500 text-xs whitespace-nowrap">
                    {new Date(log.at).toLocaleString("en-KE")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </PlatformLayout>
  );
}

function Card({ label, value }) {
  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/80 p-4">
      <p className="text-xs text-slate-400 font-medium">{label}</p>
      <p className="text-2xl font-bold text-white mt-1">{value}</p>
    </div>
  );
}

function Panel({ title, children }) {
  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/80 p-5">
      <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] mb-4">
        {title}
      </h2>
      {children}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div>
      <p className="text-slate-500 text-xs">{label}</p>
      <p className="font-semibold text-slate-100">{value}</p>
    </div>
  );
}
