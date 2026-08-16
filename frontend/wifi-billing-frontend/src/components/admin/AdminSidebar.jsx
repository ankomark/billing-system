import { NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchProfile } from "../../services/account";
import { getImpersonation, getUser, logout } from "../../services/auth";
import {
  LayoutDashboard, Users, Package, FileText, AlertCircle,
  Settings, MessageSquare, LogOut, Activity, Router,
  RefreshCw, Search, Wifi, ShieldAlert, HeartPulse, X,
  UserPlus, CreditCard, UserCog, MapPin, TrendingUp, Receipt, PlugZap,
  MailWarning,
} from "lucide-react";

/**
 * Royal green down the panel, with white as a sheen across the top.
 *
 * White is a sheen and not a stop for the usual reason — twenty nav labels live
 * on this surface — but the numbers here are the other way round from the page
 * behind it. A deep green barely lightens slate-900 at all, so it takes 0.69
 * before the section labels stop clearing 4.5:1 and the wash can be generous.
 * White reaches its limit at 0.14 and has to stay a highlight.
 *
 * Which is also why the inks below moved up. The nav labels were slate-500 and
 * the section labels slate-400 — both already under 4.5 on the flat panel,
 * before any of this — and a wash would have buried them. At slate-300 they
 * clear 7.08 even under the sheen at its brightest, which is what makes the
 * green affordable.
 *
 * Green in the sidebar is the point where blue stops being this console's
 * accent in any real sense. The active nav pill is still blue and so is the
 * chrome, but the surface a person actually sees now reads green. Worth
 * knowing, because the blue/teal split is what tells an operator's console from
 * the platform's — see tokens.js.
 */
const ASIDE = {
  backgroundColor: "#0f172a",
  backgroundImage: [
    "linear-gradient(180deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0) 18%)",
    "linear-gradient(180deg, rgba(20,83,45,0.55) 0%, rgba(6,95,70,0.42) 46%, rgba(15,23,42,0) 100%)",
  ].join(","),
  backgroundRepeat: "no-repeat",
};

export default function AdminSidebar({ open, onClose }) {
  const navigate = useNavigate();

  // Impersonation wins: while platform staff are viewing as an operator, the
  // console should name that operator, not the staff member's own tenant
  // (which is null for platform accounts anyway).
  const impersonation = getImpersonation();
  const cached = getUser();

  // Staff read the business; admins change it. Hiding what they cannot reach
  // is kinder than a 403 they could have been spared — and the routes enforce
  // it too, so this is presentation, not security.
  const isAdmin = cached?.role !== "tenant_staff";

  /**
   * Read live, not from the copy stored at sign-in.
   *
   * The cached user is written once at login and never again, so when the
   * platform owner corrects an operator's business name the operator kept
   * seeing the old one until they happened to sign out — on the sidebar of
   * every page, which is the most visible place the name appears. The cached
   * value seeds it so there is no flash of "Operator" while this loads, and no
   * request at all while impersonating, where the name is already decided.
   */
  const { data: profile } = useQuery({
    queryKey: ["profile"],
    queryFn: fetchProfile,
    enabled: !impersonation,
    placeholderData: cached,
  });

  const operatorName =
    impersonation?.name || profile?.tenant_name || cached?.tenant_name || "Operator";

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar */}
      <aside
        style={ASIDE}
        className={`
          fixed top-0 left-0 z-40 h-full w-64 border-r border-white/10 flex flex-col transform transition-transform duration-300 ease-in-out
          lg:static lg:translate-x-0 lg:z-auto lg:flex-shrink-0
          ${open ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        {/* Brand */}
        <div className="px-5 py-4 border-b border-white/10 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center flex-shrink-0">
              <Wifi size={16} className="text-white" />
            </div>
            <div>
              {/* Whose console this is. It used to read "Skylink Admin" for
                  every operator on the platform, which showed one operator's
                  brand to all the others. It comes from the signed-in tenant
                  now, or from the operator being impersonated. */}
              <p className="text-white font-bold text-sm leading-tight">
                {operatorName}
              </p>
              <p className="text-slate-300 text-xs">Management Portal</p>
            </div>
          </div>
          {/* Mobile close button */}
          <button
            onClick={onClose}
            className="lg:hidden text-slate-500 hover:text-white transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-3 px-3 space-y-0.5">
          <SectionLabel label="Overview" />
          <NavItem to="/admin/dashboard"      icon={LayoutDashboard} label="Dashboard"     onClick={onClose} />
          <NavItem to="/admin/analytics"      icon={TrendingUp}      label="Analytics"     onClick={onClose} />

          <SectionLabel label="Billing" />
          <NavItem to="/admin/customers"       icon={Users}         label="Customers"       onClick={onClose} />
          {isAdmin && (
            <NavItem to="/admin/customers/new"   icon={UserPlus}      label="Add Customer"    onClick={onClose} />
          )}
          <NavItem to="/admin/packages"        icon={Package}       label="Packages"        onClick={onClose} />
          <NavItem to="/admin/invoices/unpaid" icon={FileText}      label="Unpaid Invoices" onClick={onClose} />
          <NavItem to="/admin/mpesa/transactions" icon={Receipt}    label="M-Pesa Payments" onClick={onClose} />
          <NavItem to="/admin/mpesa/failed"    icon={AlertCircle}   label="M-Pesa Errors"  onClick={onClose} />

          <SectionLabel label="Network" />
          <NavItem to="/admin/pppoe/sessions"  icon={Activity}      label="PPPoE Sessions"  onClick={onClose} />
          <NavItem to="/admin/routers"         icon={Router}        label="Routers"         onClick={onClose} />
          <NavItem to="/admin/router-health"   icon={HeartPulse}    label="Router Health"   onClick={onClose} />
          <NavItem to="/admin/failover-logs"   icon={RefreshCw}     label="Failover Logs"   onClick={onClose} />
          <NavItem to="/admin/usage-alerts"    icon={ShieldAlert}   label="Usage Alerts"    onClick={onClose} />

          <SectionLabel label="Communications" />
          {isAdmin && (
            <NavItem to="/admin/broadcast"     icon={MessageSquare} label="Broadcast"       onClick={onClose} />
          )}
          {/* Next to Broadcast, which is where an operator is standing when
              they find out a send did not work. */}
          <NavItem to="/admin/messages"        icon={MailWarning}   label="Message Delivery" onClick={onClose} />
          <NavItem to="/admin/access-lookup"   icon={Search}        label="Access Lookup"   onClick={onClose} />
          <NavItem to="/admin/failed-connections" icon={PlugZap}   label="Failed Connections" onClick={onClose} />

          <SectionLabel label="System" />
          {isAdmin && (
            <>
              <NavItem to="/admin/settings"    icon={Settings}      label="Settings"        onClick={onClose} />
              <NavItem to="/admin/stations"    icon={MapPin}        label="Stations"        onClick={onClose} />
              <NavItem to="/admin/team"        icon={Users}         label="Team"            onClick={onClose} />
            </>
          )}
          <NavItem to="/admin/account"         icon={UserCog}       label="My Account"      onClick={onClose} />
          {/* What this operator owes the platform. Stays reachable while
              restricted — it is the page that explains why. */}
          <NavItem to="/admin/billing"         icon={CreditCard}    label="Subscription"    onClick={onClose} />
        </nav>

        {/* Logout */}
        <div className="p-3 border-t border-white/10">
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-slate-300 hover:text-white hover:bg-red-600/90 transition-colors text-sm font-medium"
          >
            <LogOut size={15} />
            Sign Out
          </button>
        </div>
      </aside>
    </>
  );
}

function SectionLabel({ label }) {
  return (
    <p className="px-3 pt-5 pb-1.5 text-xs font-semibold text-slate-300 uppercase tracking-widest select-none">
      {label}
    </p>
  );
}

function NavItem({ to, icon: Icon, label, onClick }) {
  return (
    <NavLink
      to={to}
      onClick={onClick}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
          isActive
            ? "bg-blue-600 text-white shadow-sm"
            : "text-slate-300 hover:text-white hover:bg-white/5"
        }`
      }
    >
      <Icon size={15} className="flex-shrink-0" />
      {label}
    </NavLink>
  );
}
