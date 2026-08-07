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

export const createRouter = async (payload) => {
  const res = await api.post("admin/routers/", payload);
  return res.data;
};

/** Send `password` only when changing it — blank means "keep the current one". */
export const updateRouter = async (id, payload) => {
  const res = await api.patch(`admin/routers/${id}/`, payload);
  return res.data;
};

/** Refused by the backend while subscribers are still assigned to it. */
export const deleteRouter = async (id) => {
  await api.delete(`admin/routers/${id}/`);
};

/**
 * Register a router and get back the commands that finish setting it up.
 *
 * For hardware behind CGNAT, which is nearly all of it — the platform cannot
 * dial a router that has no public address, so it needs a WireGuard tunnel.
 * Setting one up by hand meant SSHing to the server for its public key,
 * generating a keypair on the router, copying the public half back, and
 * running a script as root, before this form could be filled in at all.
 *
 * This does all of that and returns one block of RouterOS commands. Paste it
 * into WinBox, press Test connection, done.
 *
 * The block contains the router's private key and the API password, and is
 * returned once — the backend stores neither. Losing it costs one more
 * provision, not a site visit.
 */
export const provisionRouter = async (payload) => {
  const res = await api.post("admin/routers/provision/", payload);
  return res.data;
};

/**
 * Dial the router and report back before anything is saved.
 *
 * Takes either typed-in details or `{ router_id }` for one already stored —
 * a saved password never comes back to the browser, so re-testing an existing
 * router sends the id and lets the backend use what it has.
 */
export const testRouter = async (payload) => {
  const res = await api.post("admin/routers/test/", payload);
  return res.data;
};

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
