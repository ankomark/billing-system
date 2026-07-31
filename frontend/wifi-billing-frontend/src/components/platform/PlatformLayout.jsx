import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  Activity, Building2, LayoutDashboard, LogOut, Menu, Receipt, ShieldCheck,
  Tag, X,
} from "lucide-react";
import { getUser, logout } from "../../services/auth";
import { PLATFORM_NAME, PLATFORM_TAGLINE } from "../../constants/brand";
import ImpersonationBanner from "./ImpersonationBanner";

/**
 * Chrome for the platform owner's own dashboard.
 *
 * Visually distinct from the operator dashboard on purpose. Someone with both
 * kinds of access needs to know at a glance whose data they are looking at —
 * a slate operator console versus this darker platform one.
 */
export default function PlatformLayout({ children }) {
  const navigate = useNavigate();
  const user = getUser();
  const [open, setOpen] = useState(false);

  const signOut = () => {
    logout();
    navigate("/login");
  };

  const close = () => setOpen(false);

  return (
    <div className="flex min-h-screen bg-slate-950">
      {/* Backdrop for the mobile drawer. The operator console has had one since
          it was written; this console was desktop-only, which on a phone meant
          a 240px sidebar permanently eating the screen. */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
          onClick={close}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 w-60 flex-shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col
          transform transition-transform duration-300 ease-in-out
          lg:static lg:translate-x-0 lg:z-auto
          ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-teal-600 rounded-lg grid place-items-center">
              <ShieldCheck size={16} className="text-white" />
            </div>
            <div className="min-w-0">
              <p className="text-white font-bold text-sm leading-tight truncate">
                {PLATFORM_NAME}
              </p>
              <p className="text-slate-500 text-xs">{PLATFORM_TAGLINE}</p>
            </div>
            <button
              onClick={close}
              className="ml-auto lg:hidden text-slate-500 hover:text-white transition-colors"
              aria-label="Close menu"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-3 px-3 space-y-0.5">
          <Item to="/platform" end icon={LayoutDashboard} label="Overview" onClick={close} />
          <Item to="/platform/operators" icon={Building2} label="Operators" onClick={close} />
          <Item to="/platform/invoices" icon={Receipt} label="Invoices" onClick={close} />
          <Item to="/platform/plans" icon={Tag} label="Plans" onClick={close} />
          <Item to="/platform/health" icon={Activity} label="Health" onClick={close} />
          <Item to="/platform/audit" icon={ShieldCheck} label="Audit log" onClick={close} />
        </nav>

        <div className="p-3 border-t border-slate-800">
          <p className="px-3 pb-2 text-xs text-slate-500 truncate">
            {user?.username}
          </p>
          <button
            onClick={signOut}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-slate-400 hover:text-white hover:bg-red-600/90 transition-colors text-sm font-medium"
          >
            <LogOut size={15} />
            Sign out
          </button>
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        {/* Only way back to the navigation on a small screen. */}
        <div className="lg:hidden flex items-center gap-3 border-b border-slate-800 bg-slate-900 px-4 py-3">
          <button
            onClick={() => setOpen(true)}
            className="text-slate-300 hover:text-white transition-colors"
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>
          <span className="text-sm font-semibold text-white truncate">
            {PLATFORM_NAME}
          </span>
        </div>

        <ImpersonationBanner />
        {/* The console is dark throughout, not a dark rail around a light page.
            The operator console stays light, which is the whole point: someone
            with both kinds of access can tell at a glance whose data this is. */}
        <main className="flex-1 overflow-auto p-4 sm:p-6 lg:p-8 bg-slate-950">
          {children}
        </main>
      </div>
    </div>
  );
}

function Item({ to, icon: Icon, label, end, onClick }) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onClick}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
          isActive
            ? "bg-teal-600 text-white"
            : "text-slate-400 hover:text-white hover:bg-slate-800"
        }`
      }
    >
      <Icon size={15} className="flex-shrink-0" />
      {label}
    </NavLink>
  );
}
