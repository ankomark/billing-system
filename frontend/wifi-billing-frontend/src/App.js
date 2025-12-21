import { Routes, Route } from "react-router-dom";

// ✅ Auth
import Login from "./pages/Login";

// ✅ Admin
import Dashboard from "./pages/admin/Dashboard";
import Packages from "./pages/admin/Packages";
import PackageForm from "./pages/admin/PackageForm";
import Customers from "./pages/admin/Customers";
import CustomerDetail from "./pages/admin/CustomerDetail";
import UnpaidInvoices from "./pages/admin/UnpaidInvoices";
import FailedMpesa from "./pages/admin/FailedMpesa";
import HotspotSuccess from "./pages/hotspot/HotspotSuccess";
import ProtectedRoute from "./components/ProtectedRoute";
import PPPoEPortal from "./pages/customer/PPPoEPortal";
import PPPoERenew from "./pages/customer/PPPoERenew";
import SystemSettings from "./pages/admin/SystemSettings";
import Broadcast from "./pages/admin/Broadcast";
// ✅ Hotspot (PUBLIC)
import HotspotPackages from "./pages/hotspot/HotspotPackages";
import HotspotStatus from "./pages/hotspot/HotspotStatus";
import PPPoESessions from "./pages/admin/PPPoESessions";
import Routers from "./pages/admin/Routers";
import FailoverLogs from "./pages/admin/FailoverLogs"; // ✅ FIXED: removed "../"
import AccessLookup from "./pages/admin/AccessLookup";
function App() {
  return (
    <Routes>
      {/* 🔓 PUBLIC ROUTES */}
      <Route path="/login" element={<Login />} />

      {/* ✅ HOTSPOT CAPTIVE PORTAL */}
      <Route path="/hotspot" element={<HotspotPackages />} />
      <Route path="/hotspot/status" element={<HotspotStatus />} />
      <Route path="/hotspot/success" element={<HotspotSuccess />} />

      {/* 🔐 ADMIN ROUTES */}
      <Route
        path="/admin/dashboard"
        element={
          <ProtectedRoute allowedRoles={["admin", "staff", "superadmin"]}>
            <Dashboard />
          </ProtectedRoute>
        }
      />
      <Route 
        path="/admin/routers" 
        element={
          <ProtectedRoute allowedRoles={["admin", "superadmin", "staff"]}>
            <Routers />
          </ProtectedRoute>
        }
      />
      <Route 
        path="/admin/failover-logs" 
        element={
          <ProtectedRoute allowedRoles={["admin", "superadmin"]}>
            <FailoverLogs />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/settings"
        element={
          <ProtectedRoute allowedRoles={["admin", "superadmin"]}>
            <SystemSettings />
          </ProtectedRoute>
        }
      />
      <Route 
        path="/admin/access-lookup" 
        element={
          <ProtectedRoute allowedRoles={["admin", "superadmin"]}>
        <AccessLookup />
        </ProtectedRoute>} 
        />
      <Route
        path="/admin/broadcast"
        element={
          <ProtectedRoute allowedRoles={["admin", "superadmin"]}>
            <Broadcast />
          </ProtectedRoute>
        }
      />
      {/* ✅ PACKAGES */}
      <Route
        path="/admin/packages"
        element={
          <ProtectedRoute allowedRoles={["admin", "staff"]}>
            <Packages />
          </ProtectedRoute>
        }
      />

      <Route
        path="/admin/packages/new"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <PackageForm />
          </ProtectedRoute>
        }
      />

      <Route
        path="/admin/packages/:id"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <PackageForm />
          </ProtectedRoute>
        }
      />

      {/* ✅ CUSTOMERS */}
      <Route
        path="/admin/customers"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Customers />
          </ProtectedRoute>
        }
      />

      <Route
        path="/admin/customers/:id"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <CustomerDetail />
          </ProtectedRoute>
        }
      />

      {/* ✅ FINANCE */}
      <Route
        path="/admin/invoices/unpaid"
        element={
          <ProtectedRoute allowedRoles={["admin", "staff"]}>
            <UnpaidInvoices />
          </ProtectedRoute>
        }
      />

      <Route
        path="/admin/mpesa/failed"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <FailedMpesa />
          </ProtectedRoute>
        }
      />
      <Route 
        path="/admin/pppoe/sessions" 
        element={
          <ProtectedRoute allowedRoles={["admin", "superadmin"]}>
            <PPPoESessions />
          </ProtectedRoute>
        }
      />
      <Route
        path="/customer/pppoe"
        element={
          <ProtectedRoute allowedRoles={["customer"]}>
            <PPPoEPortal />
          </ProtectedRoute>
        }
     />
     <Route
        path="/customer/pppoe/renew"
        element={
          <ProtectedRoute allowedRoles={["customer"]}>
            <PPPoERenew />
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

export default App;