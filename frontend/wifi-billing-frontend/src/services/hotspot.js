import api from "./api";

// ✅ Public endpoint (no JWT)
export const fetchHotspotPackages = async () => {
  const res = await api.get("packages/");
  return res.data;
};

// ✅ Trigger STK Push for hotspot
export const hotspotPay = async (payload) => {
  const res = await api.post("mpesa/stk-push/", payload);
  return res.data;
};
export async function fetchHotspotUsageDaily(days = 7) {
  const res = await api.get(`/hotspot/usage/daily/?days=${days}`);
  return res.data;
}