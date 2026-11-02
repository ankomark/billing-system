import api from "./api";
import { isPlatformRole } from "../constants/roles";

const IMPERSONATE_KEY = "impersonate";

/**
 * Step one of signing in: the password.
 *
 * The answer is one of three shapes:
 *   { access, refresh, role, … }            signed in
 *   { mfa_required: true, ticket }           now ask for a code
 *   { mfa_setup_required: true, ticket }     this role must set up two-step
 * The ticket is good for five minutes and only for the next step.
 */
export const login = async (username, password) => {
  const response = await api.post("auth/login/", { username, password });
  return response.data;
};

/** Step two: the ticket from step one and a code (or a backup code). */
export const loginWithCode = async (ticket, code) => {
  const res = await api.post("auth/login/mfa/", { ticket, code });
  return res.data;
};

// Setting up two-step. With a ticket during sign-in, or without one from the
// account page once signed in.
export const startMfaSetup = async (ticket) =>
  (await api.post("auth/mfa/setup/", ticket ? { ticket } : {})).data;

export const confirmMfaSetup = async (code, ticket) =>
  (await api.post("auth/mfa/confirm/", ticket ? { ticket, code } : { code })).data;

/** Keep the tokens from a finished sign-in. */
export const storeSession = (data) => {
  localStorage.setItem("access_token", data.access);
  localStorage.setItem("refresh_token", data.refresh);
};

const clearLocalSession = () => {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("user");
  // Never leave an impersonation active across sign-ins — the next person at
  // this browser would silently be scoped to someone else's business.
  stopImpersonating();
};

/**
 * Sign out — on the server as well as here. Clearing this browser's copy is
 * all signing out used to do, so a token copied before that kept working
 * until it expired. Best effort: an offline browser still forgets its tokens.
 */
export const logout = () => {
  const refresh = localStorage.getItem("refresh_token");
  if (getToken()) {
    api.post("auth/logout/", { refresh }).catch(() => {});
  }
  clearLocalSession();
};

/** End every session this account has, on every device, this one included. */
export const logoutEverywhere = async () => {
  await api.post("auth/logout-everywhere/");
  clearLocalSession();
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
