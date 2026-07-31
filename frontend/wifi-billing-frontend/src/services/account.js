import api from "./api";

// The signed-in account: its own profile and its own password.

export const fetchProfile = async () => {
  const res = await api.get("auth/profile/");
  return res.data;
};

export const updateProfile = async (payload) => {
  const res = await api.patch("auth/profile/", payload);
  return res.data;
};

/**
 * Changing a password ends every other session, including this browser's old
 * token — so the response carries a replacement pair. Store it, or the next
 * request would 401 as a reward for succeeding.
 */
export const changePassword = async ({ current_password, new_password }) => {
  const res = await api.post("auth/change-password/", {
    current_password,
    new_password,
  });
  if (res.data?.access) {
    localStorage.setItem("access_token", res.data.access);
    localStorage.setItem("refresh_token", res.data.refresh);
  }
  return res.data;
};

// ─── An operator admin managing their own staff ─────────────────────────────
// Scoped to the caller's operator by the backend, not by anything sent here.

export const fetchUsers = async () => {
  const res = await api.get("users/");
  return Array.isArray(res.data) ? res.data : res.data?.results ?? [];
};

export const createUser = async (payload) => {
  const res = await api.post("users/", payload);
  return res.data;
};

export const updateUser = async (id, payload) => {
  const res = await api.patch(`users/${id}/`, payload);
  return res.data;
};

/** Disables rather than deletes — the backend keeps the row and its history. */
export const disableUser = async (id) => {
  await api.delete(`users/${id}/`);
};
