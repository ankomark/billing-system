import { Navigate } from "react-router-dom";
import { getUser, isAuthenticated } from "../services/auth";

function getRoleHome(role) {
  if (role === "customer") return "/customer/pppoe";
  return "/admin/dashboard";
}

export default function ProtectedRoute({ children, allowedRoles }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  const user = getUser();

  if (allowedRoles && !allowedRoles.includes(user?.role)) {
    // Authenticated but wrong role — send to their own home, not back to /login
    return <Navigate to={getRoleHome(user?.role)} replace />;
  }

  return children;
}
