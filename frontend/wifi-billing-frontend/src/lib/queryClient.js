import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      gcTime: 5 * 60 * 1000,
      /**
       * Retry a request that might work next time; never one that cannot.
       *
       * A 4xx is an answer, not a failure to get one — a page past the end, a
       * role that is not allowed, a record that is gone. Repeating it three
       * times with backoff only makes the eventual error slower to appear,
       * and while it runs the page sits on a spinner over a decided outcome.
       * 429 is the exception: it means "later", which is what a retry is.
       */
      retry: (count, error) => {
        const s = error?.response?.status;
        if (s >= 400 && s < 500 && s !== 429) return false;
        return count < 2;
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10000),
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});
