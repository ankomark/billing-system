import api from "./api";

/**
 * The subscriber's own portal.
 *
 * These pages used to call `api` inline and, in one place, the operator's
 * admin endpoint — which answered 403 for every subscriber. Putting the calls
 * here keeps the customer-facing surface in one place, where it is obvious
 * which endpoints a subscriber is actually allowed to reach.
 */

export const fetchPortal = async () => {
  const res = await api.get("pppoe/portal/");
  return res.data;
};

/**
 * The catalogue a subscriber may renew onto.
 *
 * Not `packages/` — that one is operator staff only and returns every column
 * of the model. This is the operator's PPPoE packages, with the same explicit
 * public field list the hotspot portal gets.
 */
export const fetchRenewalPackages = async () => {
  const res = await api.get("pppoe/packages/");
  return res.data.results ?? [];
};

export const renewSubscription = async ({ packageId, phone }) => {
  const res = await api.post("pppoe/renew/", { package_id: packageId, phone });
  return res.data; // { detail, invoice_number, subscription_id }
};

/** Whether a renewal has been paid. Scoped to the caller's own invoices. */
export const fetchRenewalStatus = async (reference) => {
  const res = await api.get("pppoe/renewal-status/", { params: { ref: reference } });
  return res.data; // { status, expires_at, package }
};

export const reconnectPppoe = async () => {
  const res = await api.post("pppoe/reconnect/");
  return res.data;
};
