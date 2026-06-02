import { useState } from "react";
import { Menu, Clock, X } from "lucide-react";
import AdminSidebar from "./AdminSidebar";
import { getUser } from "../../services/auth";
import useSessionTimeout from "../../hooks/useSessionTimeout";

export default function AdminLayout({ children }) {
  const user = getUser();
  const initials = (user?.username || "A").charAt(0).toUpperCase();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { showWarning, minutesLeft, dismiss } = useSessionTimeout({ warningMinutes: 5 });

  return (
    <div className="flex min-h-screen bg-slate-50">
      <AdminSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 flex flex-col min-w-0 lg:ml-0">
        {/* Session timeout warning banner */}
        {showWarning && (
          <div className="bg-amber-50 border-b border-amber-200 px-4 py-2.5 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-amber-700 text-sm">
              <Clock size={15} className="flex-shrink-0" />
              <span>
                Your session expires in <strong>{minutesLeft} minute{minutesLeft !== 1 ? "s" : ""}</strong>.
                Any API call will automatically extend it.
              </span>
            </div>
            <button onClick={dismiss} className="text-amber-500 hover:text-amber-700 flex-shrink-0">
              <X size={15} />
            </button>
          </div>
        )}

        {/* Top header */}
        <header className="h-14 bg-white border-b border-slate-200 flex items-center justify-between px-4 sm:px-6 flex-shrink-0">
          {/* Hamburger — mobile only */}
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden text-slate-500 hover:text-slate-800 transition-colors"
          >
            <Menu size={22} />
          </button>

          {/* Spacer for desktop */}
          <div className="hidden lg:block" />

          {/* User info */}
          <div className="flex items-center gap-3">
            <div className="text-right hidden sm:block">
              <p className="text-sm font-semibold text-slate-800 leading-tight">
                {user?.username || "Admin"}
              </p>
              <p className="text-xs text-slate-400 capitalize">{user?.role || "admin"}</p>
            </div>
            <div className="h-8 w-8 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
              {initials}
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto p-4 sm:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
