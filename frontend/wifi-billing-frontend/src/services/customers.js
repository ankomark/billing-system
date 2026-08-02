import api from "./api";

export const fetchCustomers = async ({ page = 1, pageSize = 25, search = "", status = "", connectionType = "" } = {}) => {
  const params = { page, page_size: pageSize };
  if (search) params.search = search;
  if (status) params.status = status;
  if (connectionType) params.connection_type = connectionType;
  const res = await api.get("customers/", { params });
  return res.data;
};

export const fetchCustomerDetail = async (id) => {
  const res = await api.get(`customers/${id}/`);
  return res.data;
};

export const createCustomer = async (data) => {
  const res = await api.post("customers/", data);
  return res.data;
};

export const updateCustomer = async (id, data) => {
  const res = await api.patch(`customers/${id}/`, data);
  return res.data;
};

export const deleteCustomer = async (id) => {
  await api.delete(`customers/${id}/`);
};

export const suspendOrResumeCustomer = async (id, action) => {
  const res = await api.post(`admin/customers/${id}/action/`, { action });
  return res.data;
};

/**
 * Give a customer access without charging for it.
 *
 * Recorded as a payment of zero with method "comp", so it runs the same path
 * as any sale — voucher minted, access provisioned — while adding nothing to
 * revenue and staying countable in the figures. Admin only; a reason is
 * required.
 */
/**
 * Sell or give a voucher to a phone number, in one step.
 *
 * The counter version of the captive portal: no M-Pesa prompt and no waiting
 * for a callback, because the money has already changed hands — or is being
 * waived, in which case a reason is required.
 */
export const issueVoucher = async ({ packageId, phone, paidWith, reason }) => {
  const res = await api.post("admin/vouchers/issue/", {
    package_id: packageId,
    phone,
    paid_with: paidWith,
    ...(reason ? { reason } : {}),
  });
  return res.data;
};

export const compAccess = async (customerId, { packageId, reason }) => {
  const res = await api.post(`admin/customers/${customerId}/comp/`, {
    package_id: packageId,
    reason,
  });
  return res.data; // { detail, voucher_code, expires_at, connection_type }
};

export const resendVoucher = async (customerId) => {
  const res = await api.post(`admin/customers/${customerId}/resend-voucher/`);
  return res.data;
};

export const migrateCustomer = async (customerId, routerId = null) => {
  const body = { customer_id: customerId };
  if (routerId) body.router_id = routerId;
  const res = await api.post("admin/customers/migrate/", body);
  return res.data;
};
