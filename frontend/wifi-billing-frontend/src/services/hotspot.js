import api from "./api";

// The captive portal serves walk-up customers with no account and no JWT.
// Every call below hits a public endpoint and carries `t`, the operator token
// the MikroTik login page passes through. These previously used the admin
// endpoints and returned 403, so nobody could actually buy anything.

export const fetchHotspotPackages = async (tenantToken) => {
  const res = await api.get("hotspot/packages/", { params: { t: tenantToken } });
  return res.data; // { provider, results: [...] }
};

// Creates the customer, subscription and invoice, then sends the STK prompt.
export const purchaseHotspotPackage = async ({ tenantToken, packageId, phone }) => {
  const res = await api.post("hotspot/purchase/", {
    t: tenantToken,
    package_id: packageId,
    phone,
  });
  return res.data; // { reference, amount }
};

/**
 * Poll after purchase.
 *
 * `token` is what releases the voucher — purchase hands it back, and without
 * it this answers paid or unpaid but no code. Invoice numbers are a timestamp
 * and four hex characters, so before the token existed the only thing between
 * a stranger guessing one and somebody else's voucher was the rate limit.
 */
export const fetchHotspotPaymentStatus = async ({ tenantToken, reference, token }) => {
  const res = await api.get("hotspot/payment-status/", {
    params: { t: tenantToken, ref: reference, ...(token ? { token } : {}) },
  });
  return res.data; // { status, voucher_code, expires_at }
};

// Whether a device already has live access.
// { status: active | expired | pending | not_found, expires_at, voucher_code, package }
export const fetchHotspotDeviceStatus = async ({ tenantToken, mac }) => {
  const res = await api.get("hotspot/status/", {
    params: { t: tenantToken, mac },
  });
  return res.data;
};

/**
 * Redeem a code on this device.
 *
 * Accepts a voucher code, an M-Pesa receipt, or a pasted M-Pesa message. Binds
 * the device on first use, so the code stops working on any other phone.
 */
export const validateHotspotVoucher = async ({ tenantToken, code, mac }) => {
  const res = await api.post(
    "hotspot/validate/",
    { code, mac_address: mac },
    { params: { t: tenantToken } }
  );
  return res.data; // { detail, expires_at }
};

/**
 * Put a known device back online.
 *
 * The endpoint has existed since the hotspot flow was written and nothing has
 * ever called it. A phone that dropped off — power cut, walked out of range,
 * moved to another room — comes back through the captive portal, which went
 * straight to the package list and asked for money again for time already
 * paid for.
 *
 * Safe to call for a device with no access: the backend answers 403 with a
 * reason rather than granting anything.
 */
export const reconnectHotspotDevice = async ({ tenantToken, mac }) => {
  const res = await api.post("hotspot/reconnect/", { t: tenantToken, mac });
  return res.data; // { status: "allowed", expires_at }
};

export async function fetchHotspotUsageDaily(days = 7) {
  const res = await api.get("hotspot/usage/daily/", { params: { days } });
  return res.data;
}
