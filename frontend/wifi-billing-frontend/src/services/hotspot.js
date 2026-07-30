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

// Poll after purchase. The voucher code comes back only once actually paid.
export const fetchHotspotPaymentStatus = async ({ tenantToken, reference }) => {
  const res = await api.get("hotspot/payment-status/", {
    params: { t: tenantToken, ref: reference },
  });
  return res.data; // { status, voucher_code, expires_at }
};

// Whether a device already has live access.
export const fetchHotspotDeviceStatus = async ({ tenantToken, mac }) => {
  const res = await api.get("hotspot/status/", {
    params: { t: tenantToken, mac },
  });
  return res.data;
};

export async function fetchHotspotUsageDaily(days = 7) {
  const res = await api.get("hotspot/usage/daily/", { params: { days } });
  return res.data;
}
