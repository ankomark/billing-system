/**
 * Data volumes, rendered the way the operator sells them.
 *
 * Every screen that shows a cap used to render it as a whole number of
 * gigabytes, because that is all the field could hold. Now that a package can
 * be capped at 300 MB, "0 GB" is the wrong answer on four different pages, so
 * the formatting lives in one place and picks its own unit.
 *
 * Binary units throughout — 1 MB is 1024 * 1024 — because that is what
 * RouterOS counts and what the backend enforces. A display that used decimal
 * megabytes would tell a subscriber they had used 314 MB of a 300 MB bundle
 * while they were still connected.
 */

const KB = 1024;
const MB = 1024 * 1024;
const GB = 1024 * 1024 * 1024;

/** Bytes as something a person reads: "1.4 GB", "300 MB", "48 KB". */
export function humanBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n >= GB) return `${(n / GB).toFixed(2)} GB`;
  if (n >= MB) return `${(n / MB).toFixed(n / MB >= 100 ? 0 : 1)} MB`;
  return `${Math.round(n / KB)} KB`;
}

/**
 * A cap held in megabytes, rendered in whatever unit reads best.
 *
 * 0 is unlimited, and callers that want to say so in words should check
 * `unlimited` themselves rather than relying on what this returns.
 */
export function humanCapMb(mb) {
  return humanBytes((Number(mb) || 0) * MB);
}

/** Bar colour by how much of the allowance is gone. Shared so the thresholds match. */
export function usageTone(percent) {
  if (percent >= 100) return "critical";
  if (percent >= 80) return "warning";
  return "normal";
}
