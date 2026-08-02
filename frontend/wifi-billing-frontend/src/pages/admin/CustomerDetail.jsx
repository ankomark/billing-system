import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import toast from "react-hot-toast";
import { ArrowLeft, Edit, Router as RouterIcon, Gift, Send } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import CompAccessModal from "../../components/admin/CompAccessModal";
import { getUser } from "../../services/auth";
import { isOperatorAdmin } from "../../constants/roles";
import { useConfirm } from "../../components/ui/ConfirmModal";
import StatusBadge from "../../components/ui/StatusBadge";
import { Skeleton, SkeletonText } from "../../components/ui/Skeleton";
import {
  fetchCustomerDetail,
  suspendOrResumeCustomer,
  resendVoucher,
  migrateCustomer,
} from "../../services/customers";
import api from "../../services/api";

export default function CustomerDetail() {
  const { id }     = useParams();
  const navigate   = useNavigate();
  const qc         = useQueryClient();
  const { confirm, ConfirmDialog } = useConfirm();
  const [selectedRouter, setSelectedRouter] = useState("");
  const [actionLoading, setActionLoading]   = useState(false);

  const { data: customer, isLoading, isError } = useQuery({
    queryKey: ["customer", id],
    queryFn: () => fetchCustomerDetail(id),
  });

  const { data: routersData } = useQuery({
    queryKey: ["routers-list"],
    queryFn: () => api.get("admin/routers/").then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });
  const routers = Array.isArray(routersData) ? routersData : routersData?.results ?? [];

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["customer", id] });
    qc.invalidateQueries({ queryKey: ["customers"] });
  };

  const handleSuspendResume = async (action) => {
    const label = action === "suspend" ? "Suspend" : "Resume";
    const ok = await confirm({
      title: `${label} ${customer.full_name}?`,
      description:
        action === "suspend"
          ? "This will immediately cut off their internet access."
          : "This will restore their internet access.",
      confirmText: label,
      danger: action === "suspend",
    });
    if (!ok) return;

    setActionLoading(true);
    try {
      await suspendOrResumeCustomer(customer.id, action);
      toast.success(`Customer ${action === "suspend" ? "suspended" : "resumed"}`);
      invalidate();
    } catch (err) {
      toast.error(err.response?.data?.detail || `Failed to ${action} customer`);
    } finally {
      setActionLoading(false);
    }
  };

  const [comping, setComping] = useState(false);
  // Giving away what the business sells is a decision about money, so it
  // belongs to whoever answers for the money.
  const canComp = isOperatorAdmin(getUser()?.role);

  const handleResendVoucher = async () => {
    setActionLoading(true);
    try {
      const res = await resendVoucher(customer.id);
      // Messaging credentials belong to each operator and are optional. An
      // operator without them sends nothing, and "sent" would be a lie.
      toast.success(res?.detail || "Sending the code to the customer");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to send voucher");
    } finally {
      setActionLoading(false);
    }
  };

  const handleMigrate = async (routerId) => {
    const label = routerId ? "Migrate to selected router" : "Auto-migrate to best router";
    const ok = await confirm({
      title: `${label}?`,
      description: "The customer's connection will be briefly interrupted during migration.",
      confirmText: "Migrate",
    });
    if (!ok) return;

    setActionLoading(true);
    try {
      const res = await migrateCustomer(customer.id, routerId || null);
      toast.success(res.detail || "Migration successful");
      invalidate();
    } catch (err) {
      toast.error(err.response?.data?.detail || "Migration failed");
    } finally {
      setActionLoading(false);
    }
  };

  if (isLoading) return <DetailSkeleton />;

  if (isError) {
    return (
      <AdminLayout>
        <div className="text-center py-16">
          <p className="text-slate-400">Customer not found or failed to load.</p>
          <button onClick={() => navigate("/admin/customers")} className="mt-4 text-blue-600 text-sm hover:underline">
            ← Back to customers
          </button>
        </div>
      </AdminLayout>
    );
  }

  return (
    <AdminLayout>
      <CompAccessModal
        open={comping}
        customer={customer}
        onClose={() => setComping(false)}
        onDone={() => qc.invalidateQueries({ queryKey: ["customer", id] })}
      />
      <div className="space-y-6 max-w-3xl">
        <ConfirmDialog />

        {/* Header */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/admin/customers")}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-white truncate">{customer.full_name}</h1>
            <p className="text-slate-400 text-sm mt-0.5">Customer #{customer.id}</p>
          </div>
          <button
            onClick={() => navigate(`/admin/customers/${id}/edit`)}
            className="inline-flex items-center gap-1.5 px-3 py-2 border border-white/15 rounded-lg text-sm font-medium text-slate-300 hover:bg-white/5 transition-colors"
          >
            <Edit size={14} />
            Edit
          </button>
        </div>

        {/* Profile card */}
        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5">
          <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] mb-4">
            Profile
          </h2>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <InfoRow label="Phone"      value={customer.phone} />
            <InfoRow label="Connection" value={customer.connection_type} />
            <div>
              <p className="text-slate-500 text-xs mb-1">Status</p>
              <StatusBadge status={customer.status} />
            </div>
            <InfoRow label="Router"     value={customer.router_name || "Not assigned"} />
            {customer.pppoe_username && (
              <InfoRow label="PPPoE Username" value={customer.pppoe_username} />
            )}
          </div>

          <div className="flex flex-wrap gap-2 mt-5 pt-4 border-t border-white/5">
            {customer.status === "active" ? (
              <Btn color="red" onClick={() => handleSuspendResume("suspend")} loading={actionLoading}>
                Suspend
              </Btn>
            ) : (
              <Btn color="green" onClick={() => handleSuspendResume("resume")} loading={actionLoading}>
                Resume
              </Btn>
            )}
            {customer.connection_type === "hotspot" && (
              <Btn color="blue" onClick={handleResendVoucher} loading={actionLoading}>
                Resend Voucher
              </Btn>
            )}
            {canComp && (
              /*
                Outlined, not filled. Every other button on this row is a solid
                colour, and this one was solid emerald — which on a row of
                primaries reads as the recommended thing to do. It is the
                operator writing off a sale. It should be findable and it
                should not be inviting.
              */
              <button
                onClick={() => setComping(true)}
                className="inline-flex items-center gap-2 rounded-lg border border-emerald-500/40 px-4 py-2 text-sm font-semibold text-emerald-300 transition-colors hover:bg-emerald-500/10"
              >
                <Gift size={14} aria-hidden="true" />
                Give free access
              </button>
            )}
          </div>
        </div>

        {/* Data used, against what they are allowed */}
        <div className="rounded-xl border border-white/10 bg-slate-900/80 p-5 shadow-lg shadow-black/20">
          <h2 className="mb-4 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            Data
          </h2>
          <DataUsage usage={customer.data_usage} />
        </div>

        {/* Which phones are on this account */}
        <div className="rounded-xl border border-white/10 bg-slate-900/80 p-5 shadow-lg shadow-black/20">
          <h2 className="mb-4 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            Devices
          </h2>
          <Devices devices={customer.devices} />
        </div>

        {/* Router migration */}
        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5">
          <h2 className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em] mb-4">
            Router Migration
          </h2>
          <div className="flex flex-wrap gap-3 items-center">
            <Btn color="violet" onClick={() => handleMigrate(null)} loading={actionLoading}>
              <RouterIcon size={14} />
              Auto Failover
            </Btn>
            <select
              value={selectedRouter}
              onChange={(e) => setSelectedRouter(e.target.value)}
              className="border border-white/15 bg-slate-950 text-slate-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Select router…</option>
              {routers.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} {r.online || r.is_online ? "(online)" : "(offline)"}
                </option>
              ))}
            </select>
            <Btn
              color="amber"
              loading={actionLoading}
              onClick={() => {
                if (!selectedRouter) { toast.error("Select a router first"); return; }
                handleMigrate(selectedRouter);
              }}
            >
              Manual Migrate
            </Btn>
          </div>
        </div>

        {/* Subscriptions */}
        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
          <div className="px-5 py-4 border-b border-white/5">
            <h2 className="text-sm font-semibold text-slate-300">Subscriptions</h2>
          </div>
          {!customer.subscriptions?.length ? (
            <p className="px-5 py-6 text-slate-500 text-sm">No subscriptions found.</p>
          ) : (
            customer.subscriptions.map((s) => (
              <div
                key={s.id}
                className="px-5 py-4 border-b border-white/5 last:border-0 text-sm flex items-center justify-between"
              >
                <div>
                  <p className="font-medium text-white">{s.package_name || s.package}</p>
                  <p className="text-slate-500 text-xs mt-0.5">
                    Expires {new Date(s.expiry_date || s.expires_at).toLocaleDateString("en-KE")}
                  </p>
                </div>
                <StatusBadge status={s.status} />
              </div>
            ))
          )}
        </div>

        {/* Vouchers */}
        {customer.vouchers?.length > 0 && (
          <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 overflow-hidden">
            <div className="px-5 py-4 border-b border-white/5">
              <h2 className="text-sm font-semibold text-slate-300">Vouchers</h2>
            </div>
            {customer.vouchers.map((v) => (
              <div
                key={v.code}
                className="px-5 py-3 border-b border-white/5 last:border-0 flex items-center justify-between text-sm"
              >
                <code className="bg-white/5 text-slate-300 px-2 py-0.5 rounded text-xs">
                  {v.code}
                </code>
                <div className="flex items-center gap-3">
                  <StatusBadge status={v.is_active ? "active" : "expired"} />
                  <span className="text-slate-500 text-xs">
                    Expires {new Date(v.expires_at).toLocaleDateString("en-KE")}
                  </span>
                  {/* Beside the code, because this is the moment somebody asks
                      for it again. The button at the top of the page does the
                      same thing, but you have to know it is there and that it
                      means this code. */}
                  {v.is_active && (
                    <button
                      onClick={handleResendVoucher}
                      disabled={actionLoading}
                      title="Send this code to the customer"
                      aria-label={`Send ${v.code} to ${customer.full_name}`}
                      className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-blue-500/10 hover:text-blue-300 disabled:opacity-40"
                    >
                      <Send size={14} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AdminLayout>
  );
}

function InfoRow({ label, value }) {
  return (
    <div>
      <p className="text-slate-500 text-xs mb-0.5">{label}</p>
      <p className="font-medium text-white capitalize">{value || "—"}</p>
    </div>
  );
}

/**
 * How much they have used, and how much they are allowed.
 *
 * An unlimited plan still shows the number. An operator asking why one
 * subscriber is saturating a tower needs to see consumption whether or not
 * there is a ceiling to compare it against.
 */
function DataUsage({ usage }) {
  if (!usage) return <p className="text-sm text-slate-500">No usage recorded yet.</p>;

  const gb = (bytes) => (bytes || 0) / 1024 ** 3;
  const fmt = (bytes) => {
    const g = gb(bytes);
    if (g >= 1) return `${g.toFixed(2)} GB`;
    const mb = (bytes || 0) / 1024 ** 2;
    if (mb >= 1) return `${mb.toFixed(1)} MB`;
    return `${((bytes || 0) / 1024).toFixed(0)} KB`;
  };

  const pct = usage.percent_used;
  const over = pct != null && pct >= 100;
  const near = pct != null && pct >= 80 && !over;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-2xl font-bold text-white">{fmt(usage.used_bytes)}</p>
        <p className="text-sm text-slate-400">
          {usage.unlimited ? "of unlimited" : `of ${usage.cap_gb} GB`}
        </p>
      </div>

      {!usage.unlimited && (
        <>
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-white/10"
            role="progressbar"
            aria-valuenow={Math.min(pct ?? 0, 100)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className={`h-full rounded-full ${
                over ? "bg-red-500" : near ? "bg-amber-500" : "bg-blue-500"
              }`}
              style={{ width: `${Math.min(pct ?? 0, 100)}%` }}
            />
          </div>
          <p className={`text-xs ${over ? "text-red-300" : near ? "text-amber-300" : "text-slate-500"}`}>
            {pct != null ? `${pct}% used` : "—"}
            {over && " · over their allowance"}
          </p>
        </>
      )}

      <div className="flex gap-6 border-t border-white/5 pt-3 text-xs text-slate-500">
        <span>Down <span className="text-slate-300">{fmt(usage.download_bytes)}</span></span>
        <span>Up <span className="text-slate-300">{fmt(usage.upload_bytes)}</span></span>
      </div>

      {usage.since && (
        <p className="text-[11px] text-slate-600">
          Since this subscription started,{" "}
          {new Date(usage.since).toLocaleString("en-KE", {
            day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
          })}
        </p>
      )}
    </div>
  );
}

/**
 * The phones on this account, against what the package allows.
 *
 * "Already in use on 2 devices" is what a third phone is told, so the operator
 * on the line to that customer needs to be looking at the same thing.
 */
function Devices({ devices }) {
  if (!devices) return null;
  const used = devices.in_use ?? [];
  const full = used.length >= devices.allowed;

  return (
    <div className="space-y-3">
      <p className="text-sm">
        <span className={`font-semibold ${full ? "text-amber-300" : "text-white"}`}>
          {used.length} of {devices.allowed}
        </span>
        <span className="text-slate-500">
          {" "}device{devices.allowed === 1 ? "" : "s"} used
        </span>
      </p>

      {used.length === 0 ? (
        <p className="text-sm text-slate-500">
          Nothing has connected yet. The first phone to use the code claims a
          place.
        </p>
      ) : (
        <ul className="space-y-2">
          {used.map((d) => (
            <li
              key={d.mac_address}
              className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-2 last:border-0 last:pb-0"
            >
              <code className="font-mono text-xs text-slate-300">{d.mac_address}</code>
              <span className="whitespace-nowrap text-[11px] text-slate-500">
                since{" "}
                {new Date(d.first_seen).toLocaleString("en-KE", {
                  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
                })}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Btn({ color, children, onClick, loading }) {
  const colors = {
    red:    "bg-red-600 hover:bg-red-700",
    green:  "bg-emerald-600 hover:bg-emerald-700",
    blue:   "bg-blue-600 hover:bg-blue-700",
    violet: "bg-violet-600 hover:bg-violet-700",
    amber:  "bg-amber-500/100 hover:bg-amber-600",
  };
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`${colors[color]} text-white px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 transition-colors inline-flex items-center gap-2`}
    >
      {children}
    </button>
  );
}

function DetailSkeleton() {
  return (
    <AdminLayout>
      <div className="space-y-6 max-w-3xl">
        <div className="flex items-center gap-3">
          <Skeleton className="w-8 h-8 rounded-lg" />
          <div className="flex-1"><Skeleton className="h-7 w-48 mb-1" /><Skeleton className="h-4 w-24" /></div>
        </div>
        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5">
          <Skeleton className="h-3 w-16 mb-4" />
          <div className="grid grid-cols-2 gap-4"><SkeletonText /><SkeletonText /><SkeletonText /><SkeletonText /></div>
        </div>
        <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-5">
          <Skeleton className="h-3 w-24 mb-4" /><SkeletonText lines={2} />
        </div>
      </div>
    </AdminLayout>
  );
}
