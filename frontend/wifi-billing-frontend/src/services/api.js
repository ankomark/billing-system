import axios from "axios";

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

// Auto-refresh on 401; force logout if refresh fails
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;

    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      const refresh = localStorage.getItem("refresh_token");
      if (!refresh) {
        localStorage.clear();
        window.location.href = "/login";
        return Promise.reject(error);
      }

      try {
        const res = await axios.post(`${BASE_URL}auth/refresh/`, { refresh });
        const newToken = res.data.access;
        localStorage.setItem("access_token", newToken);
        // Dispatch storage event so useSessionTimeout reacts immediately
        window.dispatchEvent(new Event("storage"));
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      } catch {
        localStorage.clear();
        window.location.href = "/login";
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
