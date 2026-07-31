import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { AlertTriangle, ArrowLeft, Eye, KeyRound, ShieldAlert, ShieldCheck } from "lucide-react";
import PlatformLayout from "../../components/platform/PlatformLayout";
import ResetPasswordModal from "../../components/platform/ResetPasswordModal";
import AnalyticsPanel from "../../components/platform/AnalyticsPanel";
import MpesaSetupPanel from "../../components/platform/MpesaSetupPanel";
import AuditTrail from "../../components/platform/AuditTrail";
import OperatorPlanPanel from "../../components/platform/OperatorPlanPanel";
import { Skeleton } from "../../components/ui/Skeleton";
import { getUser, startImpersonating } from "../../services/auth";
import { PLATFORM_OWNER } from "../../constants/roles";
import {
  fetchOperator,
  fetchOperatorStatusHistory,
  setOperatorStatus,
  warnOperator,
} from "../../services/platform";
import { KES, StatusBadge, STATUS_STYLES } from "../../components/platform/ui";

/**
 * Standing is one toggle plus one exit.
 *
 * It briefly offered all four statuses the API accepts, which read as four
 * similar buttons with no obvious difference between them. Two of them did not
 * belong there:
 *
 *   past_due is derived, not decided. The nightly sweep sets it from invoice
 *   due dates and a payment clears it, so setting it by hand produces a record
 *   that disagrees with the invoices and gets overwritten by whichever runs
 *   next. It is shown as a state, never offered as an action.
 *
 *   cancelled is not another word for restricted. Both lock the dashboard, but
 *   invoice generation excludes cancelled operators — a restricted operator is
 *   still being billed while you chase them, a cancelled one has left and stops
 *   accruing invoices. That is a real decision, just a rare and final one, so
 *   it sits apart from the toggle rather than beside it.
 */

const STATUS_MESSAGES = {
  active: "Access restored",
  restricted: "Operator restricted",
  cancelled: "Account closed — they will not be invoiced again",
};

export default function OperatorDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [warning, setWarning] = useState("");
  const isOwner = getUser()?.role === PLATFORM_OWNER;

  const { data: op, isLoading } = useQuery({
    queryKey: ["platform-operator", id],
    queryFn: () => fetchOperator(id),
  });

  // Implemented and tested on the backend since phase 6, and never displayed.
  const { data: history } = useQuery({
    queryKey: ["platform-operator-status", id],
    queryFn: () => fetchOperatorStatusHistory(id),
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
      toast.success(STATUS_MESSAGES[status] || "Status updated");
      setReason("");
      qc.invalidateQueries({ queryKey: ["platform-operator", id] });
      qc.invalidateQueries({ queryKey: ["platform-operator-status", id] });
      qc.invalidateQueries({ queryKey: ["platform-operators"] });
    } catch (e) {
      toast.error(e.response?.data?.detail || "Couldn't change status");
    } finally {
      setBusy(false);
    }
  };

  const sendWarning = async () => {
    if (!warning.trim()) {
      toast.error("Say what the warning is about.");
      return;
    }
    setBusy(true);
    try {
      const res = await warnOperator(id, warning.trim());
      toast.success(
        res.delivered
          ? "Warning sent and recorded"
          : `Warning recorded, but not sent — ${res.reason_no_delivery}`
      );
      setWarning("");
      qc.invalidateQueries({ queryKey: ["platform-operator-status", id] });
    } catch (e) {
      toast.error(e.response?.data?.detail || "Couldn't send the warning");
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
      <ResetPasswordModal
        open={resetting}
        operator={op}
        onClose={() => setResetting(false)}
      />
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

        <OperatorPlanPanel operator={op} canEdit={isOwner} />

        <MpesaSetupPanel operatorId={op.id} canEdit={isOwner} />

        <AnalyticsPanel tenant={op.id} title="Their trends" />

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
            {isOwner && (
              <button onClick={() => setResetting(true)} disabled={busy}
                      className="inline-flex items-center gap-2 border border-white/15 hover:bg-white/5 text-slate-200 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50">
                <KeyRound size={14} /> Reset password
              </button>
            )}
          </div>

          {/* Standing: one toggle, plus a separate exit. See the note at the
              top of this file for why past_due is not a button. */}
          <div className="mt-4 pt-4 border-t border-white/10">
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <span className="text-xs font-medium text-slate-400">Standing</span>
              <StatusBadge status={op.status} styles={STATUS_STYLES} />
              {op.status === "past_due" && (
                <span className="text-xs text-slate-500">
                  set automatically from their unpaid invoices
                </span>
              )}
            </div>

            {op.status === "cancelled" ? (
              <>
                <button
                  onClick={() => changeStatus("active")}
                  disabled={busy}
                  className="inline-flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 transition-colors"
                >
                  <ShieldCheck size={14} aria-hidden="true" /> Reopen account
                </button>
                <p className="text-xs text-slate-500 mt-2">
                  This account is closed and is not being invoiced. Reopening
                  restores access and puts them back on the billing run.
                </p>
              </>
            ) : op.is_restricted ? (
              <>
                <button
                  onClick={() => changeStatus("active")}
                  disabled={busy}
                  className="inline-flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 transition-colors"
                >
                  <ShieldCheck size={14} aria-hidden="true" /> Restore access
                </button>
                <p className="text-xs text-slate-500 mt-2">
                  Gives them their dashboard back.
                </p>
              </>
            ) : (
              <>
                <button
                  onClick={() => changeStatus("restricted")}
                  disabled={busy}
                  className="inline-flex items-center gap-2 bg-red-500 hover:bg-red-400 text-slate-950 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 transition-colors"
                >
                  <ShieldAlert size={14} aria-hidden="true" /> Restrict
                </button>
                <p className="text-xs text-slate-500 mt-2">
                  Locks them out of their dashboard, and nothing else. Their
                  subscribers keep their internet, walk-up customers can still
                  buy, money still reaches their till, and they keep being
                  invoiced.
                </p>
              </>
            )}
          </div>

          {/* Closing an account is rare, final, and does something restricting
              does not — it takes them off the billing run. Kept away from the
              toggle so it cannot be hit by accident. */}
          {isOwner && op.status !== "cancelled" && (
            <details className="mt-4 pt-4 border-t border-white/10 group">
              <summary className="text-xs text-slate-500 hover:text-slate-300 cursor-pointer select-none">
                They have left the platform
              </summary>
              <div className="mt-3">
                <p className="text-xs text-slate-500 mb-2">
                  Closing stops invoices being generated for them, which
                  restricting does not — a restricted operator keeps accruing a
                  bill while you chase it. Their subscribers are unaffected
                  either way.
                </p>
                <button
                  onClick={() => changeStatus("cancelled")}
                  disabled={busy}
                  className="inline-flex items-center gap-2 border border-red-500/30 text-red-300 hover:bg-red-500/10 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 transition-colors"
                >
                  Close this account
                </button>
              </div>
            </details>
          )}
        </Panel>

        <Panel title="Send a warning">
          <p className="text-sm text-slate-400 mb-3">
            Goes on the record and is texted to their contact number. Nothing
            changes about their access — this is the step before restricting,
            which until now did not exist.
          </p>
          <textarea
            value={warning}
            onChange={(e) => setWarning(e.target.value)}
            rows={2}
            placeholder="Invoice PINV-0002 is 9 days overdue. Please settle it to avoid your dashboard being locked."
            className="w-full border border-white/15 bg-slate-950 text-slate-100 placeholder-slate-600 rounded-lg px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-teal-500"
          />
          <button
            onClick={sendWarning}
            disabled={busy}
            className="inline-flex items-center gap-2 bg-amber-500 hover:bg-amber-400 text-slate-950 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 transition-colors"
          >
            <AlertTriangle size={14} aria-hidden="true" /> Send warning
          </button>
        </Panel>

        <Panel title="Standing history">
          {!history?.history?.length ? (
            <p className="text-sm text-slate-500">
              Nothing has changed this operator's standing.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {history.history.map((h, i) => (
                <li key={i} className="flex justify-between gap-4 border-b border-white/10 pb-2.5 last:border-0 last:pb-0">
                  <span className="text-sm min-w-0">
                    <span className="text-slate-200">
                      {h.from === h.to ? (
                        <span className="text-amber-300">Warned</span>
                      ) : (
                        <>
                          <span className="capitalize">{String(h.from).replace("_", " ")}</span>
                          {" → "}
                          <span className="capitalize font-medium">
                            {String(h.to).replace("_", " ")}
                          </span>
                        </>
                      )}
                    </span>
                    <span className="text-slate-500">
                      {" "}· {h.automatic ? "automatic" : h.by || "unknown"}
                    </span>
                    {h.reason && (
                      <span className="block text-xs text-slate-500 mt-0.5">{h.reason}</span>
                    )}
                  </span>
                  <span className="text-slate-500 text-xs whitespace-nowrap">
                    {new Date(h.at).toLocaleString("en-KE")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Account changes">
          <AuditTrail tenant={op.id} compact />
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
