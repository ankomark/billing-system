import axios from "axios";

const BASE_URL = process.env.REACT_APP_API_URL || "http://127.0.0.1:8000/api/";

const api = axios.create({
  baseURL: BASE_URL,
});

// Attach JWT to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
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
// NOTE: backend URLs for access-lookup/access-deactivate are missing the /api/
// prefix. They will be fixed in the backend session. Meanwhile these use the
// correct param names and endpoint paths that match the view logic.

export const accessLookup = (query) =>
  api.get("admin/access-lookup/", { params: { q: query } });

// Revoke access for a subscription (works for both PPPoE and voucher customers)
export const revokeAccess = (subscriptionId, reason) =>
  api.post("admin/access-deactivate/", { subscription_id: subscriptionId, reason });
