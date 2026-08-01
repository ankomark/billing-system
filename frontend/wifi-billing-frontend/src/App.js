import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import ProtectedRoute from "./components/ProtectedRoute";
import ErrorBoundary from "./components/ErrorBoundary";
import {
  CUSTOMER, OPERATOR_ROLES, OPERATOR_ADMIN_ROLES, PLATFORM_ROLES,
} from "./constants/roles";

// ─── Page-level code splitting ──────────────────────────────────────────────
const Login            = lazy(() => import("./pages/Login"));
const NotFound         = lazy(() => import("./pages/NotFound"));
const ChangePassword   = lazy(() => import("./pages/ChangePassword"));

// Admin
const Dashboard        = lazy(() => import("./pages/admin/Dashboard"));
const Analytics        = lazy(() => import("./pages/admin/Analytics"));
const Customers        = lazy(() => import("./pages/admin/Customers"));
const CustomerDetail   = lazy(() => import("./pages/admin/CustomerDetail"));
const CustomerForm     = lazy(() => import("./pages/admin/CustomerForm"));
const Packages         = lazy(() => import("./pages/admin/Packages"));
const PackageForm      = lazy(() => import("./pages/admin/PackageForm"));
const UnpaidInvoices   = lazy(() => import("./pages/admin/UnpaidInvoices"));
const FailedMpesa      = lazy(() => import("./pages/admin/FailedMpesa"));
const PPPoESessions    = lazy(() => import("./pages/admin/PPPoESessions"));
const Routers          = lazy(() => import("./pages/admin/Routers"));
const Stations         = lazy(() => import("./pages/admin/Stations"));
const RouterHealth     = lazy(() => import("./pages/admin/RouterHealth"));
const FailoverLogs     = lazy(() => import("./pages/admin/FailoverLogs"));
const UsageAlerts      = lazy(() => import("./pages/admin/UsageAlerts"));
const AccessLookup     = lazy(() => import("./pages/admin/AccessLookup"));
const Broadcast        = lazy(() => import("./pages/admin/Broadcast"));
const SystemSettings   = lazy(() => import("./pages/admin/SystemSettings"));
const MyAccount        = lazy(() => import("./pages/admin/MyAccount"));
const Users            = lazy(() => import("./pages/admin/Users"));

// Customer
const PPPoEPortal      = lazy(() => import("./pages/customer/PPPoEPortal"));
const PPPoERenew       = lazy(() => import("./pages/customer/PPPoERenew"));

// Hotspot (public)
const HotspotPackages  = lazy(() => import("./pages/hotspot/HotspotPackages"));
const HotspotPay       = lazy(() => import("./pages/hotspot/HotspotPay"));
const HotspotStatus    = lazy(() => import("./pages/hotspot/HotspotStatus"));
const HotspotSuccess   = lazy(() => import("./pages/hotspot/HotspotSuccess"));

// Platform owner
const PlatformOverview = lazy(() => import("./pages/platform/PlatformOverview"));
const Operators        = lazy(() => import("./pages/platform/Operators"));
const NewOperator      = lazy(() => import("./pages/platform/NewOperator"));
const OperatorDetail   = lazy(() => import("./pages/platform/OperatorDetail"));
const PlatformInvoices = lazy(() => import("./pages/platform/PlatformInvoices"));
const PlatformHealth   = lazy(() => import("./pages/platform/PlatformHealth"));
const PlatformPlans    = lazy(() => import("./pages/platform/PlatformPlans"));
const PlatformAudit    = lazy(() => import("./pages/platform/PlatformAudit"));
const PlatformAccount  = lazy(() => import("./pages/platform/PlatformAccount"));
const MyPlatformAccount = lazy(() => import("./pages/admin/MyPlatformAccount"));

// ─── Role sets ──────────────────────────────────────────────────────────────
// These come from constants/roles.js rather than being written out here. The
// backend renamed admin/staff/superadmin to the tenant_* and platform_* roles,
// and the literals left behind matched nothing — every admin route became
// unreachable, redirecting to a home that failed the same check.
const ADMIN_ROLES = OPERATOR_ROLES;
const SUPER_ROLES = OPERATOR_ADMIN_ROLES;

// Full-page loading fallback
/**
 * Shown before any lazy route resolves — including the light subscriber pages
 * and the dark consoles, so it must belong to neither.
 *
 * It used to be a light background holding shared Skeleton bars. Those bars
 * were darkened with the operator console and nothing here changed, so the
 * first thing anyone saw on any route was dark-on-light. The bars are local
 * and neutral now: a translucent grey reads on either ground.
 */
function PageLoader() {
  return (
    <div className="min-h-screen bg-slate-100 dark:bg-slate-950 flex items-center justify-center">
      <div className="space-y-3 w-64" role="status" aria-label="Loading">
        <div className="h-4 w-full rounded bg-slate-400/25 animate-pulse" />
        <div className="h-4 w-3/4 rounded bg-slate-400/25 animate-pulse" />
        <div className="h-4 w-1/2 rounded bg-slate-400/25 animate-pulse" />
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
    <ProtectedRoute allowedRoles={[CUSTOMER]}>
      <ErrorBoundary>{children}</ErrorBoundary>
    </ProtectedRoute>
  );
}

// The platform owner's own dashboard. Operators must never reach it.
function Platform({ children }) {
  return (
    <ProtectedRoute allowedRoles={PLATFORM_ROLES}>
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
        {/* Any signed-in account can be made to change its password, so this
            route is role-agnostic — it only requires a session. */}
        <Route path="/change-password"  element={<ProtectedRoute><ErrorBoundary><ChangePassword /></ErrorBoundary></ProtectedRoute>} />

        <Route path="/hotspot"          element={<HotspotPackages />} />
        <Route path="/hotspot/pay"      element={<HotspotPay />} />
        <Route path="/hotspot/status"   element={<HotspotStatus />} />
        <Route path="/hotspot/success"  element={<HotspotSuccess />} />

        {/* Admin — overview */}
        <Route path="/admin/dashboard"  element={<Admin><Dashboard /></Admin>} />
        <Route path="/admin/analytics"  element={<Admin><Analytics /></Admin>} />

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
        <Route path="/admin/stations"               element={<Admin roles={SUPER_ROLES}><Stations /></Admin>} />
        <Route path="/admin/router-health"          element={<Admin><RouterHealth /></Admin>} />
        <Route path="/admin/failover-logs"          element={<Admin roles={SUPER_ROLES}><FailoverLogs /></Admin>} />
        <Route path="/admin/usage-alerts"           element={<Admin><UsageAlerts /></Admin>} />

        {/* Admin — communications */}
        <Route path="/admin/broadcast"              element={<Admin roles={SUPER_ROLES}><Broadcast /></Admin>} />
        <Route path="/admin/access-lookup"          element={<Admin><AccessLookup /></Admin>} />

        {/* Admin — system */}
        <Route path="/admin/settings"               element={<Admin roles={SUPER_ROLES}><SystemSettings /></Admin>} />
        <Route path="/admin/account"                element={<Admin><MyAccount /></Admin>} />
        <Route path="/admin/team"                   element={<Admin roles={SUPER_ROLES}><Users /></Admin>} />
        {/* Reachable while restricted — it is the page explaining why. */}
        <Route path="/admin/billing"                element={<Admin roles={SUPER_ROLES}><MyPlatformAccount /></Admin>} />

        {/* Platform owner */}
        <Route path="/platform"                     element={<Platform><PlatformOverview /></Platform>} />
        <Route path="/platform/operators"           element={<Platform><Operators /></Platform>} />
        {/* Before the :id route, or "new" is parsed as an operator id. */}
        <Route path="/platform/operators/new"       element={<Platform><NewOperator /></Platform>} />
        <Route path="/platform/operators/:id"       element={<Platform><OperatorDetail /></Platform>} />
        <Route path="/platform/invoices"            element={<Platform><PlatformInvoices /></Platform>} />
        <Route path="/platform/health"              element={<Platform><PlatformHealth /></Platform>} />
        <Route path="/platform/plans"               element={<Platform><PlatformPlans /></Platform>} />
        <Route path="/platform/audit"               element={<Platform><PlatformAudit /></Platform>} />
        <Route path="/platform/account"             element={<Platform><PlatformAccount /></Platform>} />

        {/* Customer portal */}
        <Route path="/customer/pppoe"               element={<Customer><PPPoEPortal /></Customer>} />
        <Route path="/customer/pppoe/renew"         element={<Customer><PPPoERenew /></Customer>} />

        {/* 404 */}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
