/**
 * The PPPoE sessions page showed 0m for every session, always.
 *
 * RouterOS reports a session's uptime as a string with the zero units dropped
 * -- "1h17m17s", "15m", "2d3h4m5s" -- and the column ran parseInt over it.
 * parseInt stops at the first non-digit, so those became 1, 15 and 2, each of
 * which is under a minute when read as seconds. The only input that could have
 * rendered anything is one RouterOS never emits: it writes 90 minutes as
 * "1h30m", not "90m".
 */

import { uptimeSeconds } from "../pages/admin/PPPoESessions";

describe("uptimeSeconds", () => {
  test("reads the form RouterOS actually sends", () => {
    expect(uptimeSeconds("1h17m17s")).toBe(4637);
    expect(uptimeSeconds("2d3h4m5s")).toBe(183845);
    expect(uptimeSeconds("1w2d3h")).toBe(788400);
  });

  test("the values that used to collapse to zero", () => {
    // parseInt gave 15, 1 and 2 for these — all "0m" on the page.
    expect(uptimeSeconds("15m")).toBe(900);
    expect(uptimeSeconds("1h")).toBe(3600);
    expect(uptimeSeconds("2d")).toBe(172800);
  });

  test("zero units are dropped, not written as zero", () => {
    expect(uptimeSeconds("1h5s")).toBe(3605);
    expect(uptimeSeconds("3d20s")).toBe(259220);
  });

  test("accepts the other shapes some builds report", () => {
    expect(uptimeSeconds("4637")).toBe(4637);
    expect(uptimeSeconds("01:17:17")).toBe(4637);
    expect(uptimeSeconds("17:17")).toBe(1037);
  });

  test("a missing or unreadable value is zero, not NaN", () => {
    expect(uptimeSeconds(null)).toBe(0);
    expect(uptimeSeconds(undefined)).toBe(0);
    expect(uptimeSeconds("")).toBe(0);
    expect(uptimeSeconds("not a duration")).toBe(0);
    expect(uptimeSeconds("1:2:3:4")).toBe(0);
  });

  test("a fresh session is not rounded away", () => {
    // The case that made a working reconnect look like a dead session.
    expect(uptimeSeconds("45s")).toBe(45);
  });
});
