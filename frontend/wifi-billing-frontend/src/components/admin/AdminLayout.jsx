import AdminSidebar from "./AdminSidebar";
import { getUser } from "../../services/auth";

export default function AdminLayout({ children }) {
  const user = getUser();
  const initials = (user?.username || "A").charAt(0).toUpperCase();

  return (
    <div className="flex min-h-screen bg-slate-50">
      <AdminSidebar />

      <div className="flex-1 flex flex-col min-w-0">
        {/* Top header */}
        <header className="h-14 bg-white border-b border-slate-200 flex items-center justify-end px-6 flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="text-right">
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
        <main className="flex-1 overflow-auto p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
