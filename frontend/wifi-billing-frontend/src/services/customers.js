import api from "./api";

// ✅ Fetch all customers
export const fetchCustomers = async () => {
  const res = await api.get("/customers/");
  return res.data;
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