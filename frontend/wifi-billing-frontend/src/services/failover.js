import api from "./api";

export async function fetchFailoverLogs() {
  const res = await api.get("admin/routers/failovers/");
  return res.data;
}