import api from "./api";

// ✅ get all packages
export const fetchPackages = async () => {
  const res = await api.get("/packages/");
  return res.data;
};

// ✅ get ONE package (FOR EDIT)
export const fetchPackage = async (id) => {
  const res = await api.get(`/packages/${id}/`);
  return res.data;
};

// ✅ create
export const createPackage = async (data) => {
  const res = await api.post("/packages/", data);
  return res.data;
};

// ✅ update
export const updatePackage = async (id, data) => {
  const res = await api.put(`/packages/${id}/`, data);
  return res.data;
};

// ✅ delete
export const deletePackage = async (id) => {
  await api.delete(`/packages/${id}/`);
};
