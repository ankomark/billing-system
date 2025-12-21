import { NavLink, useNavigate } from "react-router-dom";
import { logout } from "../../services/auth";
import {
  LayoutDashboard,
  Users,
  Package,
  FileText,
  AlertCircle,
  Settings,
  MessageSquare,
  LogOut,
  Activity,
  Router,
  RefreshCw,
  Search 
} from "lucide-react";

export default function AdminSidebar() {
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <aside className="w-64 bg-gray-900 text-white min-h-screen p-4 flex flex-col">
      <h2 className="text-xl font-bold mb-6">Skylink Admin</h2>

      <nav className="space-y-2 flex-1">

        <NavItem to="/admin/dashboard" icon={LayoutDashboard} label="Dashboard" />
        <NavItem to="/admin/customers" icon={Users} label="Customers" />
        <NavItem to="/admin/packages" icon={Package} label="Packages" />
        <NavItem to="/admin/invoices/unpaid" icon={FileText} label="Invoices" />
        <NavItem to="/admin/mpesa/failed" icon={AlertCircle} label="M-Pesa Errors" />
        <NavItem to="/admin/broadcast" icon={MessageSquare} label="Broadcast" />
        <NavItem to="/admin/pppoe/sessions" icon={Activity} label="PPPoE Sessions"/>
        <NavItem to="/admin/routers" icon={Router} label="Routers" />
        <NavItem to="/admin/failover-logs" icon={RefreshCw} label="Failover Logs" />
        <NavItem to="/admin/access-lookup" icon={Search} label="Find customer" />



        {/* ⭐ SYSTEM SETTINGS */}
        <NavItem to="/admin/settings" icon={Settings} label="System Settings" />
      </nav>

      {/* ⭐ LOGOUT BUTTON */}
      <button
        onClick={handleLogout}
        className="flex items-center gap-3 bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded"
      >
        <LogOut size={18} />
        Logout
      </button>
    </aside>
  );
}

function NavItem({ to, icon: Icon, label }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded ${
          isActive ? "bg-blue-600" : "hover:bg-gray-700"
        }`
      }
    >
      <Icon size={18} />
      {label}
    </NavLink>
  );
}
