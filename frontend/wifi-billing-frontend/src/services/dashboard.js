import api from "./api";

export const fetchDashboardSummary = async () => {
  const res = await api.get("reports/revenue/");
  return res.data;
};

export const fetchUnpaidInvoices = async () => {
  const res = await api.get("dashboard/invoices/unpaid/");
  return res.data;
};

export const fetchFailedMpesa = async () => {
  const res = await api.get("dashboard/mpesa/failed/");
  return res.data;
};
export async function fetchAdminUsageDaily(days = 7) {
  const res = await api.get("admin/usage/daily/", { params: { days } });
  return res.data;
}