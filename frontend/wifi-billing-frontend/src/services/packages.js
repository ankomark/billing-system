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
  const res = await api.patch(`packages/${id}/`, data);
  return res.data;
};

// ✅ delete
export const deletePackage = async (id) => {
  await api.delete(`packages/${id}/`);
};

/**
 * Retire a package from sale, or put it back.
 *
 * What "delete" nearly always means: stop offering this one. Existing
 * subscribers keep what they bought until it expires, and the invoices that
 * name it stay intact.
 */
export const archivePackage = async (id) => {
  const res = await api.post(`packages/${id}/archive/`);
  return res.data;
};
