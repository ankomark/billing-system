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
