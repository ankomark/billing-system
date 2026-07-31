import api from "./api";

export const fetchRouterHealth = async () => {
  const res = await api.get("admin/routers/health/");
  return res.data;
};

export const fetchFailoverLogs = async () => {
  const res = await api.get("admin/routers/failovers/");
  return res.data;
};
export async function fetchRouters() {
  const res = await api.get("admin/routers/");
  return res.data;
}

/**
 * Per-router event history and availability over a window.
 *
 * RouterDevice carries only current state and one last_error that each new
 * failure overwrites, so this is the only way to see whether a router has been
 * flapping or how long it was actually down.
 */
export const fetchRouterEvents = async (days = 7) => {
  const res = await api.get("admin/routers/events/", { params: { days } });
  return res.data;
};

// ─── Stations ───────────────────────────────────────────────────────────────
// An operator's physical sites. Grouping only — one login, one bill, one till,
// whether they run one site or five.

export const fetchStations = async () => {
  const res = await api.get("stations/");
  return Array.isArray(res.data) ? res.data : res.data?.results ?? [];
};

export const createStation = async (payload) => {
  const res = await api.post("stations/", payload);
  return res.data;
};

export const updateStation = async (id, payload) => {
  const res = await api.patch(`stations/${id}/`, payload);
  return res.data;
};

/** Refused by the backend while routers are still assigned to it. */
export const deleteStation = async (id) => {
  await api.delete(`stations/${id}/`);
};
