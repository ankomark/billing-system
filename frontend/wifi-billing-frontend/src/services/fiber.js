import api from "./api";

/**
 * Smart Mapping. Every read is of stored state: the OLTs are polled every five
 * minutes by the backend, never from the browser.
 */
const get = (url, params) => api.get(url, { params }).then((r) => r.data);

export const fetchFiberOverview = () => get("fiber/overview/");
export const fetchOlts = () => get("fiber/olts/");
export const createOlt = (body) => api.post("fiber/olts/", body).then((r) => r.data);
export const updateOlt = (id, body) => api.patch(`fiber/olts/${id}/`, body).then((r) => r.data);
export const deleteOlt = (id) => api.delete(`fiber/olts/${id}/`);
export const pollOlt = (id) => api.post(`fiber/olts/${id}/poll/`).then((r) => r.data);

export const fetchOnus = (params) => get("fiber/onus/", params);
export const fetchOnu = (id, days = 14) => get(`fiber/onus/${id}/`, { days });
export const updateOnu = (id, body) => api.patch(`fiber/onus/${id}/`, body).then((r) => r.data);
export const locateOnu = (id, body) => api.post(`fiber/onus/${id}/locate/`, body).then((r) => r.data);
export const linkCustomer = (id, customer) =>
  api.post(`fiber/onus/${id}/link/`, { customer }).then((r) => r.data);

export const fetchFiberMap = () => get("fiber/map/");
export const fetchSplitters = (olt) => get("fiber/splitters/", { olt });
export const createSplitter = (body) => api.post("fiber/splitters/", body).then((r) => r.data);
export const fetchSplitterSuggestions = (olt) => get("fiber/splitters/suggest/", { olt });

export const fetchOutages = (days = 30) => get("fiber/outages/", { days });
export const fetchOutage = (id) => get(`fiber/outages/${id}/`);

export const fetchMaintenance = () => get("fiber/maintenance/");
export const createMaintenance = (body) => api.post("fiber/maintenance/", body).then((r) => r.data);
export const deleteMaintenance = (id) => api.delete(`fiber/maintenance/${id}/`);

export const fetchTickets = (status = "open") => get("fiber/tickets/", { status });
export const createTicket = (body) => api.post("fiber/tickets/", body).then((r) => r.data);
export const updateTicket = (id, body) => api.patch(`fiber/tickets/${id}/`, body).then((r) => r.data);

export const fetchPortReport = (days = 30) => get("fiber/reports/ports/", { days });
export const fetchMonthlyReport = (month) => get("fiber/reports/monthly/", { month });
