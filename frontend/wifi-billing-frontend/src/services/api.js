import axios from "axios";
import deviceId from "../lib/deviceId";
import { requestStepUp } from "../lib/stepUp";

const BASE_URL = process.env.REACT_APP_API_URL || "http://127.0.0.1:8000/api/";

const api = axios.create({
  baseURL: BASE_URL,
});

// Attach JWT to every request, plus the impersonation header when platform
// staff are viewing as an operator. The header narrows what the request can
// see; it never widens it, and the backend ignores it for non-platform
// accounts and records every use.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  const device = deviceId();
  if (device) config.headers["X-Device-Id"] = device;

  const raw = localStorage.getItem("impersonate");
  if (raw) {
    try {
      const { id, reason } = JSON.parse(raw);
      if (id) {
        config.headers["X-Impersonate-Tenant"] = String(id);
        if (reason) config.headers["X-Impersonate-Reason"] = reason;
      }
    } catch {
      // Corrupt value — behave as though not impersonating.
    }
  }
  return config;
});

const signOutHere = () => {
  // Keep the device id: it is how the next sign-in from this browser is
  // recognised as the same device.
  const device = localStorage.getItem("device_id");
  localStorage.clear();
  if (device) localStorage.setItem("device_id", device);
  window.location.href = "/login";
};

/**
 * Renew the session, once, however many requests are waiting for it.
 *
 * Each refresh token now works for exactly one renewal — the server hands
 * back a new one and ends the old. Several requests failing at once used to
 * each renew with the same token; now the second would be refused and sign
 * the user out. So they all share one renewal.
 *
 * Another tab may have renewed first. Before giving up, check whether the
 * stored refresh token changed while this one was in flight, and use that.
 */
let renewing = null;

async function renew() {
  const refresh = localStorage.getItem("refresh_token");
  if (!refresh) throw new Error("no refresh token");
  try {
    const res = await axios.post(`${BASE_URL}auth/refresh/`, { refresh });
    localStorage.setItem("access_token", res.data.access);
    if (res.data.refresh) localStorage.setItem("refresh_token", res.data.refresh);
    // Dispatch storage event so useSessionTimeout reacts immediately
    window.dispatchEvent(new Event("storage"));
    return res.data.access;
  } catch (err) {
    const now = localStorage.getItem("refresh_token");
    const access = localStorage.getItem("access_token");
    if (now && now !== refresh && access) return access;
    throw err;
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    const status = error.response?.status;

    // A sensitive action wants a fresh two-step code. Ask, then try again.
    if (
      status === 403 &&
      error.response?.data?.code === "mfa_step_up_required" &&
      !original._stepUp
    ) {
      original._stepUp = true;
      try {
        await requestStepUp();
      } catch {
        return Promise.reject(error); // cancelled: the action did not happen
      }
      return api(original);
    }

    if (status === 401 && !original._retry && !original.url?.startsWith("auth/login")) {
      original._retry = true;
      try {
        renewing = renewing || renew().finally(() => {
          renewing = null;
        });
        const newToken = await renewing;
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      } catch {
        signOutHere();
        return Promise.reject(error);
      }
    }

    return Promise.reject(error);
  }
);

export default api;

// ─── ACCESS LOOKUP ──────────────────────────────────────────────────────────
// Both endpoints are routed under /api/ and resolve correctly — verified
// against the running backend. A previous note here claimed the prefix was
// missing; it is not, and the thing that made it look missing is that
// access-lookup answers a query it cannot match with its own 404,
// {"detail": "No access record found"}, which is not a routing failure.

export const accessLookup = (query) =>
  api.get("admin/access-lookup/", { params: { q: query } });

// Revoke access for a subscription (works for both PPPoE and voucher customers)
export const revokeAccess = (subscriptionId, reason) =>
  api.post("admin/access-deactivate/", { subscription_id: subscriptionId, reason });
