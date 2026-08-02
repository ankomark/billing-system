import { Wifi } from "lucide-react";
import { PLATFORM_NAME } from "../constants/brand";

/**
 * The page everybody signs in through — platform owner, operator staff and
 * subscribers alike.
 *
 * It read "Skylink WiFi", which is one operator's business. Every other
 * operator on the platform was shown a competitor's name while typing their
 * password, and the platform owner was shown a customer's. PLATFORM_NAME
 * exists for exactly this: the platform's own identity, kept apart from any
 * operator's branding.
 */
export default function AuthLayout({ children }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 via-blue-950 to-slate-900 p-4">
      <div className="w-full max-w-md">
        {/* Brand */}
        <div className="mb-8 text-center">
          <div className="mb-4 inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-600 shadow-xl">
            <Wifi size={30} className="text-white" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            {PLATFORM_NAME}
          </h1>
          <p className="mt-1 text-sm text-slate-400">Internet billing, run properly</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl bg-white p-8 shadow-2xl">{children}</div>

        <p className="mt-6 text-center text-xs text-slate-500">
          © {new Date().getFullYear()} {PLATFORM_NAME}. All rights reserved.
        </p>
      </div>
    </div>
  );
}
