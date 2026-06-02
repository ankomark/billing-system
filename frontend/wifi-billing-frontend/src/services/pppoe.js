import api from "./api";

export async function fetchPPPoELiveStatus() {
  const res = await api.get("pppoe/live-status/");
  return res.data;
}
export async function fetchAdminPPPoESessions() {
  const res = await api.get("admin/pppoe/sessions/");
  return res.data;
}
export async function fetchPPPoEUsageDaily(days = 7) {
  const res = await api.get("pppoe/usage/daily/", { params: { days } });
  return res.data;
}

export async function fetchPPPoEUsageMonthly(months = 6) {
  const res = await api.get("pppoe/usage/monthly/", { params: { months } });
  return res.data;
}