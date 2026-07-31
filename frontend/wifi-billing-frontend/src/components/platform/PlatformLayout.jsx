import { NavLink, useNavigate } from "react-router-dom";
import {
  Activity, Building2, LayoutDashboard, LogOut, Receipt, ShieldCheck,
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

  const signOut = () => {
    logout();
    navigate("/login");
  };

  return (
    <div className="flex min-h-screen bg-slate-950">
      <aside className="w-60 flex-shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col">
        <div className="px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-teal-600 rounded-lg grid place-items-center">
              <ShieldCheck size={16} className="text-white" />
            </div>
            <div>
              <p className="text-white font-bold text-sm leading-tight">{PLATFORM_NAME}</p>
              <p className="text-slate-500 text-xs">{PLATFORM_TAGLINE}</p>
            </div>
          </div>
        </div>

        <nav className="flex-1 py-3 px-3 space-y-0.5">
          <Item to="/platform" end icon={LayoutDashboard} label="Overview" />
          <Item to="/platform/operators" icon={Building2} label="Operators" />
          <Item to="/platform/invoices" icon={Receipt} label="Invoices" />
          <Item to="/platform/health" icon={Activity} label="Health" />
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
        <ImpersonationBanner />
        {/* The console is dark throughout, not a dark rail around a light page.
            The operator console stays light, which is the whole point: someone
            with both kinds of access can tell at a glance whose data this is. */}
        <main className="flex-1 overflow-auto p-6 lg:p-8 bg-slate-950">
          {children}
        </main>
      </div>
    </div>
  );
}

function Item({ to, icon: Icon, label, end }) {
  return (
    <NavLink
      to={to}
      end={end}
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
