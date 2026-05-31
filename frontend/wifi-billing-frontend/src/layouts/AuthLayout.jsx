import { Wifi } from "lucide-react";

export default function AuthLayout({ children }) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-950 to-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4 shadow-xl">
            <Wifi size={30} className="text-white" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Skylink WiFi</h1>
          <p className="text-slate-400 text-sm mt-1">ISP Management Platform</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-2xl p-8">
          {children}
        </div>

        <p className="text-center text-slate-500 text-xs mt-6">
          © {new Date().getFullYear()} Skylink WiFi. All rights reserved.
        </p>
      </div>
    </div>
  );
}
