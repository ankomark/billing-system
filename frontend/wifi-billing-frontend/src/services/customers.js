import api from "./api";

// ✅ Fetch customers (paginated)
export const fetchCustomers = async (page = 1, pageSize = 25) => {
  const res = await api.get("/customers/", {
    params: { page, page_size: pageSize },
  });
  return res.data; // { count, total_pages, current_page, next, previous, results }
};

// ✅ Fetch ONE customer (DETAIL PAGE)
export const fetchCustomerDetail = async (id) => {
  const res = await api.get(`/customers/${id}/`);
  return res.data;
};

// ✅ Delete customer
export const deleteCustomer = async (id) => {
  await api.delete(`/customers/${id}/`);
};
export const suspendOrResumeCustomer = async (id, action) => {
  const res = await api.post(
    `/admin/customers/${id}/action/`,
    { action }
  );
  return res.data;
};
export const resendVoucher = async (customerId) => {
  const res = await api.post(
    `/admin/customers/${customerId}/resend-voucher/`
  );
  return res.data;
};
export async function migrateCustomer(customerId) {
  const res = await api.post(
    `/admin/customers/${customerId}/migrate/`
  );
  return res.data;
}