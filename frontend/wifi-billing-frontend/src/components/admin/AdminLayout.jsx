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

/**
 * An ambient wash for the page behind the cards.
 *
 * NOT CURRENTLY IN USE. The dashboard was the only page that took it, and the
 * operator console is now one flat #020617 throughout — the same ground every
 * other page already stood on. Kept, with its measurements, because the option
 * is a word on one JSX tag and the numbers below are the expensive part. Pass
 * `ambient` to bring it back.
 *
 * Four glows, in the hues asked for: azure, teal, royal blue, and white as a
 * specular bloom rather than a field. White is the one that cannot be taken
 * literally — a white BAND here would erase the page heading and the section
 * labels, which sit directly on this background with nothing behind them. As a
 * small bloom at low alpha it reads as the light catching a surface, which is
 * what makes a gradient look expensive rather than loud.
 *
 * Every alpha is roughly half of its own measured ceiling, where the ceiling is
 * the point at which slate-400 — the section labels, the weakest ink on this
 * surface — stops clearing 4.5:1 against the glow at full strength:
 *
 *     azure 0.40 · teal 0.32 · blue 0.59 · white 0.20
 *     using 0.26 ·      0.19 ·      0.36 ·       0.08
 *
 * Under the ceiling rather than at it, because these overlap: two glows meeting
 * at their edges compound, and the ceilings above are each measured alone. The
 * focal points sit at opposite corners for the same reason.
 *
 * The bloom is the one that had to be solved rather than estimated. It sits
 * over the azure and directly behind the page heading, and the two together are
 * not the sum of two safe numbers: at 0.11 the pair put the subtitle on 4.24
 * and it stopped passing, even though each was well inside its own ceiling.
 * 0.08 is the most white that corner takes while slate-400 holds 4.68. This is
 * the whole reason overlaps get measured instead of reasoned about.
 *
 * Note for later: teal is the platform console's accent, and it is now in this
 * console's background. The blue chrome still carries the distinction, but this
 * spends some of it — see tokens.js for what that distinction is for.
 */
const AMBIENT = {
  backgroundColor: "#020617",
  backgroundImage: [
    "radial-gradient(1200px 620px at 6% -8%, rgba(0,128,255,0.26), transparent 62%)",
    "radial-gradient(900px 520px at 97% 8%, rgba(20,184,166,0.19), transparent 60%)",
    "radial-gradient(1100px 720px at 52% 106%, rgba(29,78,216,0.36), transparent 66%)",
    "radial-gradient(460px 300px at 18% 2%, rgba(255,255,255,0.08), transparent 70%)",
  ].join(","),
  backgroundRepeat: "no-repeat",
  backgroundSize: "cover",
  // Anchored to the viewport, not to the scroll height. Left to scroll with
  // the content, "cover" stretches the four glows across the whole page: on a
  // dashboard two screens tall the teal and the blue end up below the fold and
  // all anyone ever sees is the azure corner. Fixed keeps the composition
  // framed the way it was designed, and keeps it still while the cards move.
  backgroundAttachment: "fixed",
};

export default function AdminLayout({ children, ambient = false }) {
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
        <main
          className={`flex-1 overflow-auto p-4 sm:p-6 lg:p-8 ${ambient ? "" : "bg-slate-950"}`}
          style={ambient ? AMBIENT : undefined}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
