import api from "./api";

// ------------------------------------------------------
export async function fetchSystemSettings() {
  const res = await api.get("system/settings/");
  return res.data;
}

// ------------------------------------------------------
export async function updateSystemSettings(data) {
  const res = await api.put("system/settings/", data);
  return res.data;
}

// ------------------------------------------------------
export async function testMpesa() {
  const res = await api.get("system/test/mpesa/");
  return res.data;
}

// ------------------------------------------------------
export async function testSms() {
  const res = await api.get("system/test/sms/");
  return res.data;
}

// ------------------------------------------------------
export async function testWhatsapp() {
  const res = await api.get("system/test/whatsapp/");
  return res.data;
}
