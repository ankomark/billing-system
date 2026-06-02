import { useNavigate } from "react-router-dom";
import { Home, ArrowLeft } from "lucide-react";
import { getUser, isAuthenticated } from "../services/auth";

export default function NotFound() {
  const navigate = useNavigate();
  const user = isAuthenticated() ? getUser() : null;
  const home = user?.role === "customer" ? "/customer/pppoe" : "/admin/dashboard";

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
      <div className="text-center max-w-md">
        <p className="text-8xl font-black text-slate-200 leading-none mb-4">404</p>
        <h1 className="text-2xl font-bold text-slate-800 mb-2">Page not found</h1>
        <p className="text-slate-500 text-sm mb-8">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="flex items-center justify-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="inline-flex items-center gap-2 px-4 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-100 transition-colors"
          >
            <ArrowLeft size={15} />
            Go back
          </button>
          {user && (
            <button
              onClick={() => navigate(home)}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 rounded-lg text-sm font-medium text-white hover:bg-blue-700 transition-colors"
            >
              <Home size={15} />
              Dashboard
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
