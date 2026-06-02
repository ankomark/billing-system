import api from "./api";

// ✅ get all packages (paginated)
export const fetchPackages = async (page = 1, pageSize = 25) => {
  const res = await api.get("packages/", {
    params: { page, page_size: pageSize },
  });
  return res.data; // { count, total_pages, current_page, next, previous, results }
};

// ✅ get ONE package (FOR EDIT)
export const fetchPackage = async (id) => {
  const res = await api.get(`packages/${id}/`);
  return res.data;
};

// ✅ create
export const createPackage = async (data) => {
  const res = await api.post("packages/", data);
  return res.data;
};

// ✅ update
export const updatePackage = async (id, data) => {
  const res = await api.put(`packages/${id}/`, data);
  return res.data;
};

// ✅ delete
export const deletePackage = async (id) => {
  await api.delete(`packages/${id}/`);
};
