/**
 * Fetch every operator page's code in the background, once, after the first
 * page has drawn.
 *
 * Pages are split into their own files so the first load stays small. The
 * cost was paid later instead: the first visit to each page waited for its
 * file, and while it did React showed the full-screen loader -- the sidebar
 * vanished and came back on every first click. Fetching them all while the
 * operator is reading the dashboard makes every later click instant.
 *
 * The same import() paths as App.js, so the bundler hands back the very
 * chunks the routes use; nothing is downloaded twice. When the browser is
 * idle, and skipped entirely on a connection the user has asked to save.
 */
const PAGES = [
  () => import("../pages/admin/Customers"),
  () => import("../pages/admin/CustomerDetail"),
  () => import("../pages/admin/Analytics"),
  () => import("../pages/admin/Packages"),
  () => import("../pages/admin/MpesaTransactions"),
  () => import("../pages/admin/UnpaidInvoices"),
  () => import("../pages/admin/FailedMpesa"),
  () => import("../pages/admin/Routers"),
  () => import("../pages/admin/RouterHealth"),
  () => import("../pages/admin/Stations"),
  () => import("../pages/admin/PPPoESessions"),
  () => import("../pages/admin/MessageLogs"),
  () => import("../pages/admin/AccessLookup"),
  () => import("../pages/admin/UsageAlerts"),
  () => import("../pages/admin/ConnectionAttempts"),
  () => import("../pages/admin/Broadcast"),
  () => import("../pages/admin/SystemSettings"),
  () => import("../pages/admin/MyAccount"),
  () => import("../pages/admin/Users"),
  () => import("../pages/admin/CustomerForm"),
  () => import("../pages/admin/PackageForm"),
  () => import("../pages/admin/FailoverLogs"),
  () => import("../pages/admin/Fiber"),
  () => import("../pages/admin/Dashboard"),
];

let started = false;

export default function prefetchOperatorPages() {
  if (started || typeof window === "undefined") return;
  started = true;
  if (navigator.connection?.saveData) return;

  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1500));
  let i = 0;
  // One at a time, each in its own idle slot, so this never competes with
  // the page the operator is actually looking at.
  const next = () => {
    if (i >= PAGES.length) return;
    PAGES[i++]().catch(() => {}).finally(() => idle(next));
  };
  idle(next);
}
