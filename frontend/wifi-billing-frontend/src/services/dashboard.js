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

/**
 * The whole M-Pesa ledger, not only what went wrong.
 *
 * Covers both connection types — it is one callback endpoint, and the
 * connection type belongs to whoever the payment resolved to.
 */
export const fetchMpesaTransactions = async ({
  page = 1, pageSize = 25, search = "", status = "", connectionType = "", days = "",
} = {}) => {
  const params = { page, page_size: pageSize };
  if (search) params.search = search;
  if (status) params.status = status;
  if (connectionType) params.connection_type = connectionType;
  if (days) params.days = days;
  const res = await api.get("mpesa/transactions/", { params });
  return res.data;
};
export async function fetchAdminUsageDaily(days = 7) {
  const res = await api.get("admin/usage/daily/", { params: { days } });
  return res.data;
}
/**
 * Everything the analytics page shows, in one request.
 *
 * One call rather than eight on purpose: the page shows a single period across
 * every panel, and separate calls would let them disagree — a pulse from one
 * moment beside a chart from another is worse than a slower page.
 */
export const fetchAnalytics = async ({ days, from, to, station } = {}) => {
  const res = await api.get("reports/analytics/", {
    params: {
      ...(from && to ? { from, to } : { days: days ?? 30 }),
      ...(station ? { station } : {}),
    },
  });
  return res.data;
};
