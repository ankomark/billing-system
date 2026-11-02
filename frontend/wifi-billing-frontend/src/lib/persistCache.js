import { createSyncStoragePersister } from "@tanstack/query-sync-storage-persister";
import { queryClient } from "./queryClient";

/**
 * Remembering the dashboard between visits, so it opens instantly.
 *
 * Without this every visit started from nothing: a page of skeletons while
 * half a dozen reports were fetched. With it the last figures are drawn
 * straight away and replaced, a moment later, by fresh ones fetched in the
 * background — the same stale-while-revalidate React Query already does
 * between pages, carried across a reload.
 *
 * Deliberately narrow:
 *   - Only the dashboard's own summaries (money, usage, analytics, who is
 *     online). Never customer lists, invoices, transactions or anything else
 *     carrying a subscriber's name or number.
 *   - Wiped on sign-out, on an expired session, and again on every sign-in
 *     (clearPersistedCache), so the next person at a shared computer never
 *     sees the last one's business.
 *   - Kept a day at most.
 */
export const PERSIST_KEY = "sb-dashboard-cache";

const PERSISTED = new Set([
  "dashboard-summary",
  "monthly-revenue",
  "yearly-revenue",
  "operator-analytics",
  "admin-usage-daily",
  "admin-usage-totals",
  "active-clients",
]);

const storage = (() => {
  try {
    const probe = "__sb_probe__";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    return undefined; // private mode, blocked storage: just do not persist
  }
})();

export const persistOptions = {
  persister: createSyncStoragePersister({ storage, key: PERSIST_KEY, throttleTime: 1000 }),
  maxAge: 24 * 60 * 60 * 1000,
  // Bump when the shape of a remembered answer changes, so an old copy is
  // dropped rather than drawn.
  buster: "v1",
  dehydrateOptions: {
    shouldDehydrateQuery: (q) =>
      q.state.status === "success" && PERSISTED.has(q.queryKey?.[0]),
  },
};

/** Forget everything remembered: on sign-out, and before a new sign-in. */
export function clearPersistedCache() {
  try {
    localStorage.removeItem(PERSIST_KEY);
  } catch {
    /* storage unavailable: nothing was persisted */
  }
  queryClient.clear();
}
