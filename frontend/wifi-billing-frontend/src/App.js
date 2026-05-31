import { Routes, Route, Navigate } from "react-router-dom";

// Auth
import Login from "./pages/Login";
import ProtectedRoute from "./components/ProtectedRoute";

// Admin
import Dashboard from "./pages/admin/Dashboard";
import Customers from "./pages/admin/Customers";
import CustomerDetail from "./pages/admin/CustomerDetail";
import Packages from "./pages/admin/Packages";
import PackageForm from "./pages/admin/PackageForm";
import UnpaidInvoices from "./pages/admin/UnpaidInvoices";
import FailedMpesa from "./pages/admin/FailedMpesa";
import PPPoESessions from "./pages/admin/PPPoESessions";
import Routers from "./pages/admin/Routers";
import RouterHealth from "./pages/admin/RouterHealth";
import FailoverLogs from "./pages/admin/FailoverLogs";
import UsageAlerts from "./pages/admin/UsageAlerts";
import AccessLookup from "./pages/admin/AccessLookup";
import Broadcast from "./pages/admin/Broadcast";
import SystemSettings from "./pages/admin/SystemSettings";

// Customer
import PPPoEPortal from "./pages/customer/PPPoEPortal";
import PPPoERenew from "./pages/customer/PPPoERenew";

// Hotspot (public)
import HotspotPackages from "./pages/hotspot/HotspotPackages";
import HotspotPay from "./pages/hotspot/HotspotPay";
import HotspotStatus from "./pages/hotspot/HotspotStatus";
import HotspotSuccess from "./pages/hotspot/HotspotSuccess";

const ADMIN_ROLES = ["admin", "staff", "superadmin"];
const SUPER_ROLES = ["admin", "superadmin"];

function App() {
  return (
    <Routes>
      {/* Root redirect */}
      <Route path="/" element={<Navigate to="/login" replace />} />

      {/* Public */}
      <Route path="/login" element={<Login />} />
      <Route path="/hotspot" element={<HotspotPackages />} />
      <Route path="/hotspot/pay" element={<HotspotPay />} />
      <Route path="/hotspot/status" element={<HotspotStatus />} />
      <Route path="/hotspot/success" element={<HotspotSuccess />} />

      {/* Admin — overview */}
      <Route path="/admin/dashboard" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><Dashboard /></ProtectedRoute>
      } />

      {/* Admin — billing */}
      <Route path="/admin/customers" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><Customers /></ProtectedRoute>
      } />
      <Route path="/admin/customers/:id" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><CustomerDetail /></ProtectedRoute>
      } />
      <Route path="/admin/packages" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><Packages /></ProtectedRoute>
      } />
      <Route path="/admin/packages/new" element={
        <ProtectedRoute allowedRoles={SUPER_ROLES}><PackageForm /></ProtectedRoute>
      } />
      <Route path="/admin/packages/:id" element={
        <ProtectedRoute allowedRoles={SUPER_ROLES}><PackageForm /></ProtectedRoute>
      } />
      <Route path="/admin/invoices/unpaid" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><UnpaidInvoices /></ProtectedRoute>
      } />
      <Route path="/admin/mpesa/failed" element={
        <ProtectedRoute allowedRoles={SUPER_ROLES}><FailedMpesa /></ProtectedRoute>
      } />

      {/* Admin — network */}
      <Route path="/admin/pppoe/sessions" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><PPPoESessions /></ProtectedRoute>
      } />
      <Route path="/admin/routers" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><Routers /></ProtectedRoute>
      } />
      <Route path="/admin/router-health" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><RouterHealth /></ProtectedRoute>
      } />
      <Route path="/admin/failover-logs" element={
        <ProtectedRoute allowedRoles={SUPER_ROLES}><FailoverLogs /></ProtectedRoute>
      } />
      <Route path="/admin/usage-alerts" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><UsageAlerts /></ProtectedRoute>
      } />

      {/* Admin — communications */}
      <Route path="/admin/broadcast" element={
        <ProtectedRoute allowedRoles={SUPER_ROLES}><Broadcast /></ProtectedRoute>
      } />
      <Route path="/admin/access-lookup" element={
        <ProtectedRoute allowedRoles={ADMIN_ROLES}><AccessLookup /></ProtectedRoute>
      } />

      {/* Admin — system */}
      <Route path="/admin/settings" element={
        <ProtectedRoute allowedRoles={SUPER_ROLES}><SystemSettings /></ProtectedRoute>
      } />

      {/* Customer portal */}
      <Route path="/customer/pppoe" element={
        <ProtectedRoute allowedRoles={["customer"]}><PPPoEPortal /></ProtectedRoute>
      } />
      <Route path="/customer/pppoe/renew" element={
        <ProtectedRoute allowedRoles={["customer"]}><PPPoERenew /></ProtectedRoute>
      } />

      {/* 404 catch-all */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}

export default App;
