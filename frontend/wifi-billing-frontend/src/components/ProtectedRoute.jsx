import { Navigate, useLocation } from "react-router-dom";
import { getUser, isAuthenticated } from "../services/auth";
import { homeFor } from "../constants/roles";

export default function ProtectedRoute({ children, allowedRoles }) {
  const location = useLocation();

  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  const user = getUser();

  // No stored profile means a half-finished sign-in. Send them back rather
  // than bouncing between guarded routes.
  if (!user?.role) {
    return <Navigate to="/login" replace />;
  }

  // An account whose password was chosen by someone else — a platform owner
  // resetting a forgotten one, or an operator admin creating a colleague — goes
  // nowhere until it has its own. Checked before the role check so it applies
  // to every guarded route rather than to one of them.
  const CHANGE_PASSWORD = "/change-password";
  if (user.must_change_password && location.pathname !== CHANGE_PASSWORD) {
    return <Navigate to={CHANGE_PASSWORD} replace />;
  }

  if (allowedRoles && !allowedRoles.includes(user.role)) {
    const home = homeFor(user.role);
    // Guard against a redirect loop. If their own home is also forbidden the
    // roles are misconfigured, and looping is far harder to diagnose than
    // landing back on login — which is exactly what happened when the backend
    // renamed roles and the frontend still checked the old names.
    return <Navigate to={home === location.pathname ? "/login" : home} replace />;
  }

  return children;
}
