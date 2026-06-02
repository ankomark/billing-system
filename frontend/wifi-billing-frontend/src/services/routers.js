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
