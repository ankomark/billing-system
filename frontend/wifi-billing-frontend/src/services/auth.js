import api from "./api";

export const login = async (username, password) => {
  const response = await api.post("auth/login/", {
    username,
    password,
  });
  return response.data;
};

export const logout = () => {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("user");
};

export const getToken = () => localStorage.getItem("access_token");

export const getUser = () => {
  const user = localStorage.getItem("user");
  return user ? JSON.parse(user) : null;
};

export const isAuthenticated = () => !!getToken();
