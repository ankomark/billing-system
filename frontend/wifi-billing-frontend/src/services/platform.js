import api from "./api";

// The platform owner's own views — across every operator. Distinct from the
// operator-facing services, which see one business.

export const fetchPlatformOverview = async () => {
  const res = await api.get("platform/overview/");
  return res.data;
};

export const fetchOperators = async (status) => {
  const res = await api.get("platform/operators/", {
    params: status ? { status } : {},
  });
  return res.data;
};

// Onboards an operator and its first admin login in one call. Restricted to
// the platform owner by the backend — platform_staff get 403.
export const createOperator = async (payload) => {
  const res = await api.post("platform/operators/", payload);
  return res.data;
};

export const fetchOperator = async (id) => {
  const res = await api.get(`platform/operators/${id}/`);
  return res.data;
};

/**
 * Correct an operator's details after onboarding. Owner-only.
 */
export const updateOperator = async (id, payload) => {
  const res = await api.patch(`platform/operators/${id}/`, payload);
  return res.data;
};

/**
 * Set a temporary password for an operator's admin.
 *
 * The password comes back in this response and nowhere else — it is not stored
 * in readable form and not written to any log. If the caller loses it, the only
 * remedy is another reset.
 */
export const resetOperatorPassword = async (id, { username, reason } = {}) => {
  const res = await api.post(`platform/operators/${id}/reset-password/`, {
    ...(username ? { username } : {}),
    ...(reason ? { reason } : {}),
  });
  return res.data;
};

/**
 * A notice that changes nothing about their access, but is on the record and
 * texted to them. The step between saying nothing and restricting.
 */
export const warnOperator = async (id, message) => {
  const res = await api.post(`platform/operators/${id}/warn/`, { message });
  return res.data;
};

export const fetchOperatorStatusHistory = async (id) => {
  const res = await api.get(`platform/operators/${id}/status/`);
  return res.data;
};

export const setOperatorStatus = async (id, { status, reason }) => {
  const res = await api.post(`platform/operators/${id}/status/`, { status, reason });
  return res.data;
};

// Platform invoices. Platform staff see every operator's; an operator sees
// only their own, which makes this both the collections view and the
// operator's statement.
export const fetchPlatformInvoices = async ({ status, overdue } = {}) => {
  const res = await api.get("platform/invoices/", {
    params: { ...(status ? { status } : {}), ...(overdue ? { overdue: "true" } : {}) },
  });
  return res.data;
};

export const recordOperatorPayment = async ({ number, amount, method, reference }) => {
  const res = await api.post("platform/payments/", {
    number, amount, method, reference,
  });
  return res.data;
};

// What this operator owes the platform.
export const fetchMyPlatformAccount = async () => {
  const res = await api.get("platform/my-account/");
  return res.data;
};

// Always an array. platform/plans/ is a router-registered ModelViewSet, so
// unlike the hand-written platform views it comes back paginated as
// {count, results} — callers want the list, not the envelope.
export const fetchPlatformPlans = async () => {
  const res = await api.get("platform/plans/");
  return Array.isArray(res.data) ? res.data : res.data?.results ?? [];
};
