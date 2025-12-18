import api from "./api";

export async function fetchFailoverLogs() {
  const res = await api.get("/admin/failover/logs/");
  return res.data;
}