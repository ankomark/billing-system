import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import ProtectedRoute from "./components/ProtectedRoute";
import ErrorBoundary from "./components/ErrorBoundary";
import { Skeleton } from "./components/ui/Skeleton";

// ─── Page-level code splitting ──────────────────────────────────────────────
const Login            = lazy(() => import("./pages/Login"));
const NotFound         = lazy(() => import("./pages/NotFound"));

// Admin
const Dashboard        = lazy(() => import("./pages/admin/Dashboard"));
const Customers        = lazy(() => import("./pages/admin/Customers"));
const CustomerDetail   = lazy(() => import("./pages/admin/CustomerDetail"));
const CustomerForm     = lazy(() => import("./pages/admin/CustomerForm"));
const Packages         = lazy(() => import("./pages/admin/Packages"));
const PackageForm      = lazy(() => import("./pages/admin/PackageForm"));
const UnpaidInvoices   = lazy(() => import("./pages/admin/UnpaidInvoices"));
const FailedMpesa      = lazy(() => import("./pages/admin/FailedMpesa"));
const PPPoESessions    = lazy(() => import("./pages/admin/PPPoESessions"));
const Routers          = lazy(() => import("./pages/admin/Routers"));
const RouterHealth     = lazy(() => import("./pages/admin/RouterHealth"));
const FailoverLogs     = lazy(() => import("./pages/admin/FailoverLogs"));
const UsageAlerts      = lazy(() => import("./pages/admin/UsageAlerts"));
const AccessLookup     = lazy(() => import("./pages/admin/AccessLookup"));
const Broadcast        = lazy(() => import("./pages/admin/Broadcast"));
const SystemSettings   = lazy(() => import("./pages/admin/SystemSettings"));

// Customer
const PPPoEPortal      = lazy(() => import("./pages/customer/PPPoEPortal"));
const PPPoERenew       = lazy(() => import("./pages/customer/PPPoERenew"));

// Hotspot (public)
const HotspotPackages  = lazy(() => import("./pages/hotspot/HotspotPackages"));
const HotspotPay       = lazy(() => import("./pages/hotspot/HotspotPay"));
const HotspotStatus    = lazy(() => import("./pages/hotspot/HotspotStatus"));
const HotspotSuccess   = lazy(() => import("./pages/hotspot/HotspotSuccess"));

// ─── Role sets ──────────────────────────────────────────────────────────────
const ADMIN_ROLES = ["admin", "staff", "superadmin"];
const SUPER_ROLES = ["admin", "superadmin"];

// Full-page loading fallback
function PageLoader() {
  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center">
      <div className="space-y-3 w-64">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </div>
  );
}

function Admin({ children, roles = ADMIN_ROLES }) {
  return (
    <ProtectedRoute allowedRoles={roles}>
      <ErrorBoundary>{children}</ErrorBoundary>
    </ProtectedRoute>
  );
}

function Customer({ children }) {
  return (
    <ProtectedRoute allowedRoles={["customer"]}>
      <ErrorBoundary>{children}</ErrorBoundary>
    </ProtectedRoute>
  );
}

export default function App() {
  return (
    <Suspense fallback={<PageLoader />}>
      <Routes>
        {/* Root redirect */}
        <Route path="/" element={<Navigate to="/login" replace />} />

        {/* Public */}
        <Route path="/login"            element={<Login />} />
        <Route path="/hotspot"          element={<HotspotPackages />} />
        <Route path="/hotspot/pay"      element={<HotspotPay />} />
        <Route path="/hotspot/status"   element={<HotspotStatus />} />
        <Route path="/hotspot/success"  element={<HotspotSuccess />} />

        {/* Admin — overview */}
        <Route path="/admin/dashboard"  element={<Admin><Dashboard /></Admin>} />

        {/* Admin — billing */}
        <Route path="/admin/customers"              element={<Admin><Customers /></Admin>} />
        <Route path="/admin/customers/new"          element={<Admin roles={SUPER_ROLES}><CustomerForm /></Admin>} />
        <Route path="/admin/customers/:id"          element={<Admin><CustomerDetail /></Admin>} />
        <Route path="/admin/customers/:id/edit"     element={<Admin roles={SUPER_ROLES}><CustomerForm /></Admin>} />
        <Route path="/admin/packages"               element={<Admin><Packages /></Admin>} />
        <Route path="/admin/packages/new"           element={<Admin roles={SUPER_ROLES}><PackageForm /></Admin>} />
        <Route path="/admin/packages/:id"           element={<Admin roles={SUPER_ROLES}><PackageForm /></Admin>} />
        <Route path="/admin/invoices/unpaid"        element={<Admin><UnpaidInvoices /></Admin>} />
        <Route path="/admin/mpesa/failed"           element={<Admin roles={SUPER_ROLES}><FailedMpesa /></Admin>} />

        {/* Admin — network */}
        <Route path="/admin/pppoe/sessions"         element={<Admin><PPPoESessions /></Admin>} />
        <Route path="/admin/routers"                element={<Admin><Routers /></Admin>} />
        <Route path="/admin/router-health"          element={<Admin><RouterHealth /></Admin>} />
        <Route path="/admin/failover-logs"          element={<Admin roles={SUPER_ROLES}><FailoverLogs /></Admin>} />
        <Route path="/admin/usage-alerts"           element={<Admin><UsageAlerts /></Admin>} />

        {/* Admin — communications */}
        <Route path="/admin/broadcast"              element={<Admin roles={SUPER_ROLES}><Broadcast /></Admin>} />
        <Route path="/admin/access-lookup"          element={<Admin><AccessLookup /></Admin>} />

        {/* Admin — system */}
        <Route path="/admin/settings"               element={<Admin roles={SUPER_ROLES}><SystemSettings /></Admin>} />

        {/* Customer portal */}
        <Route path="/customer/pppoe"               element={<Customer><PPPoEPortal /></Customer>} />
        <Route path="/customer/pppoe/renew"         element={<Customer><PPPoERenew /></Customer>} />

        {/* 404 */}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
