import { useState } from "react";
import { Menu, Clock, X, AlertTriangle } from "lucide-react";
import AdminSidebar from "./AdminSidebar";
import { getUser } from "../../services/auth";
import ImpersonationBanner from "../platform/ImpersonationBanner";
import useSessionTimeout from "../../hooks/useSessionTimeout";

const ROLE_LABELS = {
  tenant_admin: "Admin",
  tenant_staff: "Staff",
  platform_owner: "Platform owner",
  platform_staff: "Platform staff",
};

// Copy for an operator whose standing has slipped. Restriction is
// dashboard-only: their subscribers, their income and new signups are all
// unaffected, and saying so is the difference between a warning and a scare.
const STANDING = {
  past_due: {
    tone: "bg-amber-500/10 border-amber-500/30 text-amber-800",
    title: "Payment overdue",
    body: "Settle your platform invoice to avoid losing access to this dashboard. Your customers and your income are not affected.",
  },
  restricted: {
    tone: "bg-red-500/10 border-red-500/30 text-red-800",
    title: "Your dashboard is locked",
    body: "Settle your platform invoice to restore access. Your customers keep their internet, new customers can still sign up, and payments still reach you.",
  },
  cancelled: {
    tone: "bg-red-500/10 border-red-500/30 text-red-800",
    title: "Your account has been closed",
    body: "Contact the platform to discuss reopening it. Your customers are not affected.",
  },
};

export default function AdminLayout({ children }) {
  const user = getUser();
  const initials = (user?.username || "A").charAt(0).toUpperCase();
  // Exposed on the profile so the shell could warn without a second request,
  // and then not used anywhere — an operator only learned they were past due
  // by visiting the billing page.
  const standing = STANDING[user?.tenant_status];
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { showWarning, minutesLeft, dismiss } = useSessionTimeout({ warningMinutes: 5 });

  return (
    <div className="flex min-h-screen bg-slate-950">
      <AdminSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 flex flex-col min-w-0 lg:ml-0">
        {/* Loud and always present while platform staff are viewing as this
            operator — the failure it prevents is changing a real business's
            records believing they are your own. */}
        <ImpersonationBanner />

        {standing && (
          <div className={`border-b px-4 py-2.5 flex items-start gap-2.5 ${standing.tone}`}>
            <AlertTriangle size={15} className="flex-shrink-0 mt-0.5" aria-hidden="true" />
            <p className="text-sm">
              <strong>{standing.title}.</strong> {standing.body}{" "}
              <a href="/admin/billing" className="underline font-medium whitespace-nowrap">
                View invoice
              </a>
            </p>
          </div>
        )}

        {/* Session timeout warning banner */}
        {showWarning && (
          <div className="bg-amber-500/10 border-b border-amber-500/30 px-4 py-2.5 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-amber-300 text-sm">
              <Clock size={15} className="flex-shrink-0" />
              <span>
                Your session expires in <strong>{minutesLeft} minute{minutesLeft !== 1 ? "s" : ""}</strong>.
                Any API call will automatically extend it.
              </span>
            </div>
            <button onClick={dismiss} className="text-amber-400 hover:text-amber-300 flex-shrink-0">
              <X size={15} />
            </button>
          </div>
        )}

        {/* Top header */}
        <header className="h-14 bg-slate-900 border-b border-white/10 flex items-center justify-between px-4 sm:px-6 flex-shrink-0">
          {/* Hamburger — mobile only */}
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden text-slate-400 hover:text-white transition-colors"
          >
            <Menu size={22} />
          </button>

          {/* Spacer for desktop */}
          <div className="hidden lg:block" />

          {/* User info */}
          <div className="flex items-center gap-3">
            <div className="text-right hidden sm:block">
              <p className="text-sm font-semibold text-white leading-tight">
                {user?.username || "Admin"}
              </p>
              <p className="text-xs text-slate-500">
                {ROLE_LABELS[user?.role] || user?.role || "Admin"}
              </p>
            </div>
            <div className="h-8 w-8 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
              {initials}
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto p-4 sm:p-6 lg:p-8 bg-slate-950">
          {children}
        </main>
      </div>
    </div>
  );
}
