import api from "./api";
import { isPlatformRole } from "../constants/roles";

const IMPERSONATE_KEY = "impersonate";

export const login = async (username, password) => {
  const response = await api.post("auth/login/", { username, password });
  // Also carries role, tenant_id and is_platform_staff, so choosing a
  // dashboard no longer needs a second call to /auth/profile/.
  return response.data;
};

export const logout = () => {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("user");
  // Never leave an impersonation active across sign-ins — the next person at
  // this browser would silently be scoped to someone else's business.
  stopImpersonating();
};

export const getToken = () => localStorage.getItem("access_token");

export const getUser = () => {
  const user = localStorage.getItem("user");
  return user ? JSON.parse(user) : null;
};

export const isAuthenticated = () => !!getToken();

export const isPlatformStaff = () => isPlatformRole(getUser()?.role);

// ─── Impersonation ──────────────────────────────────────────────────────────
// Platform staff viewing the platform as one operator sees it. Not a separate
// login: the same token is used, and each request carries a header that
// narrows what it can reach. The backend records every one.

export const startImpersonating = ({ id, name, reason }) => {
  localStorage.setItem(IMPERSONATE_KEY, JSON.stringify({ id, name, reason }));
};

export const stopImpersonating = () => localStorage.removeItem(IMPERSONATE_KEY);

export const getImpersonation = () => {
  const raw = localStorage.getItem(IMPERSONATE_KEY);
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
};
