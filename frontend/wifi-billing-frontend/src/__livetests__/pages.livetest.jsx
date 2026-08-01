/**
 * Smoke-renders every page against the REAL backend in Docker.
 *
 * Not part of `npm test` — it needs the compose stack up, so it would fail in
 * CI for reasons that have nothing to do with the code. Run it deliberately:
 *
 *   npx react-scripts test --testMatch "**\/*.livetest.jsx" --watchAll=false
 *
 * RUNNING IT BACK TO BACK: the login endpoint allows 5 a minute, and this
 * suite needs more than that — several tests exist precisely to prove a login
 * works (a temporary password, a newly created admin). Tokens are cached per
 * account and a 429 is waited out rather than treated as a failure, but two
 * runs in quick succession can still brush the limit. That is the throttle
 * doing its job; leave a minute between runs.
 *
 * WHAT THIS SUITE CANNOT TELL YOU: it sends requests through axios's Node
 * adapter, which performs no CORS preflight. A missing entry in
 * CORS_ALLOW_HEADERS is therefore invisible here and breaks only in a browser
 * — that is how the impersonation headers shipped broken while every test
 * passed. Anything CORS-shaped belongs in the backend suite, where
 * ImpersonationCorsTests now checks the preflight directly.
 */
import axios from "axios";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders, screen, waitFor } from "../test-utils";
import { server } from "../mocks/server";
import api from "../services/api";

// jsdom's XHR enforces CORS against an origin of "http://localhost" (no port),
// which the backend does not allow — and CORS is verified separately anyway.
// The Node adapter takes jsdom out of the transport entirely, leaving this
// suite testing what it is actually for: how the pages render real responses.
axios.defaults.adapter = "http";
api.defaults.adapter = "http";

const API = "http://127.0.0.1:8000/api/";

/**
 * The platform owner's own account — the one real person signs in with, so it
 * is the one that gets renamed and re-passworded.
 *
 * It was hardcoded as owner/devpass123, which meant the whole suite stopped
 * dead the first time the owner changed either. Everything here failed with
 * "bad credentials", pointing at the tests rather than at the fact that they
 * were spelling a username that no longer existed. Override to run it:
 *
 *   LIVETEST_OWNER=smartbill LIVETEST_OWNER_PASSWORD='…' npx react-scripts test …
 *
 * The seeded operator accounts are fixtures, not anyone's login, so they stay
 * on the default.
 */
const OWNER = process.env.LIVETEST_OWNER || "smartbill";
const DEFAULT_PASSWORD = "devpass123";
const PASSWORDS = {
  [OWNER]: process.env.LIVETEST_OWNER_PASSWORD || DEFAULT_PASSWORD,
};

// Seeded operator's public token — the hotspot portal carries no JWT and
// identifies the operator from this alone.
const TENANT_TOKEN = "0-tKzs_hCpw37qWtBo3_1YySXWsCvbhu";

// Pages under test. Anything needing a URL param is rendered at a route that
// supplies one.
// `expect` is text that must actually appear once the real response lands. It
// is what stops these tests passing vacuously against an empty database or a
// page stuck on its skeleton.
const PLATFORM_PAGES = [
  ["PlatformOverview", () => require("../pages/platform/PlatformOverview").default, /Platform overview/i],
  ["Operators", () => require("../pages/platform/Operators").default, /Skylink WiFi/i],
  ["PlatformInvoices", () => require("../pages/platform/PlatformInvoices").default, /PINV-0002/i],
  ["NewOperator", () => require("../pages/platform/NewOperator").default, /New operator/i],
  ["PlatformHealth", () => require("../pages/platform/PlatformHealth").default, /Routers offline/i],
  ["PlatformPlans", () => require("../pages/platform/PlatformPlans").default, /Starter/i],
  ["PlatformAudit", () => require("../pages/platform/PlatformAudit").default, /Audit log/i],
  ["PlatformAccount", () => require("../pages/platform/PlatformAccount").default, /owner/i],
];

const ADMIN_PAGES = [
  ["Dashboard", () => require("../pages/admin/Dashboard").default, null],
  ["Customers", () => require("../pages/admin/Customers").default, /Grace Wanjiru/i],
  ["Packages", () => require("../pages/admin/Packages").default, /Home 10Mbps/i],
  ["UnpaidInvoices", () => require("../pages/admin/UnpaidInvoices").default, null],
  ["FailedMpesa", () => require("../pages/admin/FailedMpesa").default, null],
  ["Routers", () => require("../pages/admin/Routers").default, /Main Router/i],
  ["RouterHealth", () => require("../pages/admin/RouterHealth").default, null],
  ["FailoverLogs", () => require("../pages/admin/FailoverLogs").default, null],
  ["PPPoESessions", () => require("../pages/admin/PPPoESessions").default, null],
  ["UsageAlerts", () => require("../pages/admin/UsageAlerts").default, null],
  ["AccessLookup", () => require("../pages/admin/AccessLookup").default, null],
  ["Broadcast", () => require("../pages/admin/Broadcast").default, null],
  ["SystemSettings", () => require("../pages/admin/SystemSettings").default, null],
  ["MyPlatformAccount", () => require("../pages/admin/MyPlatformAccount").default, /Skylink WiFi/i],
  ["MyAccount", () => require("../pages/admin/MyAccount").default, /Operator admin/i],
  ["Users", () => require("../pages/admin/Users").default, /skyadmin/i],
  ["Stations", () => require("../pages/admin/Stations").default, /Stations/i],
  ["Analytics", () => require("../pages/admin/Analytics").default, /Performance/i],
];

/**
 * Log in for real.
 *
 * Patient on purpose. The login endpoint is deliberately rate-limited, and
 * running this suite repeatedly trips it — a 429 here is the throttle working,
 * not a defect, so the right response is to wait it out rather than to treat it
 * as a failure. Backs off progressively up to about two minutes.
 */
const _tokenCache = new Map();

/** One login per account per run. The throttle allows 5 a minute. */
async function tokenFor(username) {
  if (!_tokenCache.has(username)) {
    _tokenCache.set(username, await login(username));
  }
  return _tokenCache.get(username);
}

async function login(username) {
  let lastErr;
  for (let i = 0; i < 30; i++) {
    try {
      const res = await axios.post(`${API}auth/login/`, {
        username,
        password: PASSWORDS[username] || DEFAULT_PASSWORD,
      });
      return res.data;
    } catch (e) {
      lastErr = e;
      const status = e.response?.status;
      // A wrong password will never come right by waiting.
      if (status === 401) throw new Error(`bad credentials for ${username}`);
      await new Promise((r) => setTimeout(r, status === 429 ? 5000 : 1500));
    }
  }
  throw new Error(
    `could not log in as ${username}: ${lastErr?.response?.status ?? lastErr?.message}`
  );
}

function authAs(tokens) {
  localStorage.setItem("access_token", tokens.access);
  localStorage.setItem("refresh_token", tokens.refresh);
  localStorage.setItem("role", tokens.role);
  if (tokens.tenant_id != null) localStorage.setItem("tenant_id", String(tokens.tenant_id));
}

let ownerTokens;
let adminTokens;
let consoleErrors;
const realConsoleError = console.error;

beforeAll(async () => {
  // Stop msw so requests reach the real backend instead of the mock handlers.
  server.close();
  ownerTokens = await tokenFor(OWNER);
  adminTokens = await tokenFor("skyadmin");
}, 300000);

afterAll(() => {
  console.error = realConsoleError;
});

beforeEach(() => {
  consoleErrors = [];
  console.error = (...args) => {
    consoleErrors.push(args.map(String).join(" "));
    realConsoleError(...args);
  };
});

afterEach(() => {
  console.error = realConsoleError;
  localStorage.clear();
});

/**
 * Renders a page and waits for its first data to land. A crash during render
 * throws and fails the test; this additionally fails on React errors that only
 * reach the console, which is where most shape mismatches surface.
 */
async function smoke(name, load, tokens, mustContain, route = "/") {
  authAs(tokens);
  const Page = load();
  renderWithProviders(<Page />, { route });

  // Give react-query time to resolve and the page to re-render with real data.
  await waitFor(
    () => {
      expect(document.body.textContent.length).toBeGreaterThan(0);
    },
    { timeout: 15000 }
  );
  await new Promise((r) => setTimeout(r, 1200));

  const fatal = consoleErrors.filter(
    (e) =>
      !e.includes("not wrapped in act") &&
      !e.includes("React Router Future Flag") &&
      !e.includes("Warning: validateDOMNesting")
  );
  if (fatal.length) {
    throw new Error(`${name} logged errors:\n${fatal.join("\n---\n")}`);
  }
  expect(screen.queryByText(/Couldn't load|Something went wrong/i)).toBeNull();

  if (mustContain) {
    // Proves the real response actually reached the DOM, rather than the page
    // sitting on a skeleton or rendering an empty state.
    expect(document.body.textContent).toMatch(mustContain);
  }
}

/**
 * Pages that read a URL param need a real route to read it from, so these
 * render inside a <Routes> rather than bare. This is where param handling and
 * detail-endpoint shapes get exercised.
 */
async function smokeRouted(name, load, tokens, path, url, mustContain) {
  authAs(tokens);
  const Page = load();
  renderWithProviders(
    <Routes>
      <Route path={path} element={<Page />} />
    </Routes>,
    { route: url }
  );

  await waitFor(() => expect(document.body.textContent.length).toBeGreaterThan(0), {
    timeout: 15000,
  });
  await new Promise((r) => setTimeout(r, 1200));

  const fatal = consoleErrors.filter(
    (e) =>
      !e.includes("not wrapped in act") &&
      !e.includes("React Router Future Flag") &&
      !e.includes("Warning: validateDOMNesting")
  );
  if (fatal.length) {
    throw new Error(`${name} logged errors:\n${fatal.join("\n---\n")}`);
  }
  if (mustContain) expect(document.body.textContent).toMatch(mustContain);
}

describe("detail and form pages against the live backend", () => {
  test("OperatorDetail renders operator 1", async () => {
    await smokeRouted(
      "OperatorDetail",
      () => require("../pages/platform/OperatorDetail").default,
      ownerTokens, "/platform/operators/:id", "/platform/operators/1",
      /Skylink WiFi/i
    );
  }, 40000);

  test("CustomerDetail renders customer 1", async () => {
    await smokeRouted(
      "CustomerDetail",
      () => require("../pages/admin/CustomerDetail").default,
      adminTokens, "/admin/customers/:id", "/admin/customers/1", null
    );
  }, 40000);

  test("CustomerForm renders in create mode", async () => {
    await smokeRouted(
      "CustomerForm",
      () => require("../pages/admin/CustomerForm").default,
      adminTokens, "/admin/customers/new", "/admin/customers/new", null
    );
  }, 40000);

  test("CustomerForm renders in edit mode", async () => {
    await smokeRouted(
      "CustomerForm",
      () => require("../pages/admin/CustomerForm").default,
      adminTokens, "/admin/customers/:id/edit", "/admin/customers/1/edit", null
    );
  }, 40000);

  test("PackageForm renders in edit mode", async () => {
    await smokeRouted(
      "PackageForm",
      () => require("../pages/admin/PackageForm").default,
      adminTokens, "/admin/packages/:id", "/admin/packages/1", null
    );
  }, 40000);
});

describe("accounts against the live backend", () => {
  test("the profile carries what the app shell needs", async () => {
    authAs(adminTokens);
    const { fetchProfile } = require("../services/account");
    const profile = await fetchProfile();
    expect(profile.username).toBe("skyadmin");
    expect(profile.tenant_name).toBeTruthy();
    // Added so the shell can show a past-due banner without a second call.
    expect(profile).toHaveProperty("tenant_status");
    expect(profile).toHaveProperty("must_change_password");
  }, 40000);

  test("an operator admin sees only their own team", async () => {
    authAs(adminTokens);
    const { fetchUsers } = require("../services/account");
    const users = await fetchUsers();
    const names = users.map((u) => u.username);
    expect(names).toContain("skyadmin");
    // blueadmin belongs to the other operator.
    expect(names).not.toContain("blueadmin");
  }, 40000);

  test("a wrong current password is refused", async () => {
    authAs(adminTokens);
    const { changePassword } = require("../services/account");
    await expect(
      changePassword({ current_password: "definitely-wrong", new_password: "N3wPassphrase!x" })
    ).rejects.toMatchObject({ response: { status: 400 } });
  }, 40000);

  test("an operator admin cannot reset anyone via the platform endpoint", async () => {
    authAs(adminTokens);
    const { resetOperatorPassword } = require("../services/platform");
    await expect(
      resetOperatorPassword(1, { reason: "should not work" })
    ).rejects.toMatchObject({ response: { status: 403 } });
  }, 40000);

  test("the owner can reset, and the temporary password works once", async () => {
    // Reset the SECOND operator so the skyadmin token the rest of this suite
    // uses is not invalidated underneath it.
    authAs(ownerTokens);
    const { resetOperatorPassword } = require("../services/platform");
    const result = await resetOperatorPassword(2, { reason: "livetest" });
    expect(result.username).toBe("blueadmin");
    expect(result.temporary_password).toHaveLength(14);

    const signedIn = await axios.post(`${API}auth/login/`, {
      username: "blueadmin",
      password: result.temporary_password,
    });
    expect(signedIn.status).toBe(200);

    // Put it back so re-running this suite stays idempotent.
    await axios.post(
      `${API}auth/change-password/`,
      { current_password: result.temporary_password, new_password: DEFAULT_PASSWORD },
      { headers: { Authorization: `Bearer ${signedIn.data.access}` } }
    );
  }, 60000);
});

describe("creating an operator against the live backend", () => {
  /**
   * Every run used to leave its operator behind. Fourteen of them accumulated
   * in the development database before anyone noticed, each with a working
   * tenant_admin login — the tests were creating real accounts and walking
   * away. Cleaned up here now that erasing one is possible; the same three
   * gates apply, so this closes the account first and types the name back.
   */
  let created;

  afterAll(async () => {
    if (!created) return;
    authAs(ownerTokens);
    const { setOperatorStatus, deleteOperator } = require("../services/platform");
    try {
      await setOperatorStatus(created.id, {
        status: "cancelled",
        reason: "Livetest cleanup",
      });
      await deleteOperator(created.id, created.name);
    } catch (e) {
      // Never fail the run over cleanup — say what was left so it can be
      // cleared by hand rather than silently accumulating again.
      console.warn(
        `[livetest] could not remove operator ${created.id} (${created.name}):`,
        e.response?.data?.detail || e.message
      );
    }
  }, 60000);

  test("owner can create one, and the new admin can sign in", async () => {
    authAs(ownerTokens);
    const { createOperator } = require("../services/platform");
    // Unique per run so repeated runs do not collide on username or slug.
    const stamp = Date.now().toString().slice(-8);
    const name = `Livetest Networks ${stamp}`;
    const op = await createOperator({
      name,
      admin_username: `lt${stamp}`,
      admin_password: DEFAULT_PASSWORD,
      pppoe_prefix: "LT",
    });
    // Recorded before the assertions, so a failure below still gets cleaned up.
    created = { id: op.id, name };
    expect(op.id).toBeGreaterThan(0);
    expect(op.status).toBe("trial");
    expect(op.slug).toMatch(/^livetest-networks-/);

    // The whole point of creating the login alongside the tenant.
    const signedIn = await axios.post(`${API}auth/login/`, {
      username: `lt${stamp}`,
      password: DEFAULT_PASSWORD,
    });
    expect(signedIn.data.role).toBe("tenant_admin");
    expect(signedIn.data.tenant_id).toBe(op.id);
  }, 60000);

  test("an operator admin is refused", async () => {
    authAs(adminTokens);
    const { createOperator } = require("../services/platform");
    await expect(
      createOperator({
        name: "Should Not Exist",
        admin_username: `nope${Date.now()}`,
        admin_password: DEFAULT_PASSWORD,
      })
    ).rejects.toMatchObject({ response: { status: 403 } });
  }, 60000);
});

describe("operator analytics, against the live backend", () => {
  test("one request carries every panel", async () => {
    authAs(adminTokens);
    const { fetchAnalytics } = require("../services/dashboard");
    const d = await fetchAnalytics({ days: 30 });

    for (const key of ["range", "pulse", "totals", "series", "by_package",
                       "by_method", "peak_hours", "expiring", "flow", "by_station"]) {
      expect(d).toHaveProperty(key);
    }
    // One call, so no two panels can describe different moments.
    expect(d.series).toHaveLength(d.range.days);
  }, 40000);

  test("every hour of the day is present, not only the ones with sales", async () => {
    authAs(adminTokens);
    const { fetchAnalytics } = require("../services/dashboard");
    const d = await fetchAnalytics({ days: 30 });
    expect(d.peak_hours.map((h) => h.hour)).toEqual([...Array(24).keys()]);
  }, 40000);

  test("packages carry the volume behind the revenue", async () => {
    authAs(adminTokens);
    const { fetchAnalytics } = require("../services/dashboard");
    const d = await fetchAnalytics({ days: 60 });
    expect(d.by_package.length).toBeGreaterThan(0);
    for (const p of d.by_package) {
      expect(p).toHaveProperty("purchases");
      expect(p).toHaveProperty("customers");
    }
  }, 40000);

  test("a backwards range is refused rather than returning nonsense", async () => {
    authAs(adminTokens);
    const { fetchAnalytics } = require("../services/dashboard");
    await expect(
      fetchAnalytics({ from: "2026-07-31", to: "2026-07-01" })
    ).rejects.toMatchObject({ response: { status: 400 } });
  }, 40000);

  test("it never reports another operator's revenue", async () => {
    const { fetchAnalytics } = require("../services/dashboard");

    authAs(adminTokens);
    const mine = await fetchAnalytics({ days: 90 });

    const blue = await tokenFor("blueadmin");
    const theirs = await axios.get(`${API}reports/analytics/?days=90`, {
      headers: { Authorization: `Bearer ${blue.access}` },
    });

    // Two real businesses with different books; identical totals would mean
    // the scoping had collapsed.
    expect(mine.totals.revenue).not.toBe(theirs.data.totals.revenue);
  }, 60000);
});

describe("the four gaps, against the live backend", () => {
  test("the audit log is actually readable now", async () => {
    authAs(ownerTokens);
    const { fetchAuditLog } = require("../services/platform");
    const rows = await fetchAuditLog({ limit: 50 });
    expect(Array.isArray(rows)).toBe(true);
    // Earlier tests in this file reset a password and wrote credentials, so
    // there is real history to read rather than an empty list proving nothing.
    if (rows.length) {
      expect(rows[0]).toHaveProperty("action");
      expect(rows[0]).toHaveProperty("at");
    }
  }, 40000);

  test("an operator sees only actions against their own business", async () => {
    authAs(adminTokens);
    const { fetchAuditLog } = require("../services/platform");
    const rows = await fetchAuditLog({ limit: 50 });
    const operators = new Set(rows.map((r) => r.operator_id));
    for (const id of operators) expect(id).toBe(1);
  }, 40000);

  test("plans can be read and an operator moved onto one", async () => {
    authAs(ownerTokens);
    const { fetchPlatformPlans, setOperatorPlan } = require("../services/platform");
    const plans = await fetchPlatformPlans();
    expect(plans.length).toBeGreaterThan(0);

    const res = await setOperatorPlan(2, plans[0].slug);
    expect(res.plan).toBe(plans[0].name);
    // The current period is deliberately untouched.
    expect(res.note).toMatch(/current period is unchanged/i);
  }, 40000);

  test("an operator cannot change their own plan", async () => {
    authAs(adminTokens);
    const { setOperatorPlan } = require("../services/platform");
    await expect(setOperatorPlan(1, "starter")).rejects.toMatchObject({
      response: { status: 403 },
    });
  }, 40000);

  test("router health is rolled up per station", async () => {
    authAs(adminTokens);
    const { fetchRouterEvents } = require("../services/routers");
    const data = await fetchRouterEvents(7);
    expect(Array.isArray(data.stations)).toBe(true);
    for (const st of data.stations) {
      expect(st).toHaveProperty("uptime_percent");
      expect(st).toHaveProperty("routers_offline");
    }
  }, 40000);

  test("analytics breaks down by station for one operator only", async () => {
    authAs(ownerTokens);
    const { fetchPlatformAnalytics } = require("../services/platform");
    const one = await fetchPlatformAnalytics({ days: 30, tenant: 1 });
    const all = await fetchPlatformAnalytics({ days: 30 });

    expect(Array.isArray(one.stations)).toBe(true);
    // Every site of every business answers nothing, so the platform-wide view
    // carries none.
    expect(all.stations).toEqual([]);
  }, 40000);
});

describe("helping an operator get paid, against the live backend", () => {
  test("the owner sees what is missing and the callback URL to register", async () => {
    authAs(ownerTokens);
    const { fetchOperatorMpesa } = require("../services/platform");
    const setup = await fetchOperatorMpesa(2);

    expect(setup).toHaveProperty("configured");
    expect(setup).toHaveProperty("missing");
    // The value Safaricom asks for, which the operator has no other way to find.
    expect(setup).toHaveProperty("callback_url");
  }, 40000);

  test("saving credentials never reads a secret back", async () => {
    authAs(ownerTokens);
    const { fetchOperatorMpesa, updateOperatorMpesa } = require("../services/platform");

    await updateOperatorMpesa(2, {
      MPESA_CONSUMER_KEY: "livetest-key",
      MPESA_CONSUMER_SECRET: "livetest-secret-never-readable",
      MPESA_SHORTCODE: "600000",
      MPESA_PASSKEY: "livetest-passkey",
    });

    const after = await fetchOperatorMpesa(2);
    expect(after.configured).toBe(true);
    expect(after.MPESA_SHORTCODE).toBe("600000");
    expect(after.MPESA_CONSUMER_SECRET).toBe("********");
    expect(JSON.stringify(after)).not.toContain("livetest-secret-never-readable");
  }, 40000);

  test("testing bad credentials reports Safaricom's refusal, not success", async () => {
    authAs(ownerTokens);
    const { testOperatorMpesa } = require("../services/platform");
    // The values above are not real, so Daraja must reject them. A pass here
    // would mean the test is not actually reaching Safaricom.
    await expect(testOperatorMpesa(2)).rejects.toMatchObject({
      response: { status: 400 },
    });
  }, 60000);

  test("an operator cannot configure another operator", async () => {
    authAs(adminTokens);
    const { updateOperatorMpesa } = require("../services/platform");
    await expect(
      updateOperatorMpesa(2, { MPESA_SHORTCODE: "999999" })
    ).rejects.toMatchObject({ response: { status: 403 } });
  }, 40000);

  afterAll(async () => {
    // Put the operator back to unconfigured. Leaving fake credentials behind
    // would make the health feed report this operator as able to take payments
    // when it cannot, which is worse than the gap it was reporting before.
    authAs(ownerTokens);
    const { updateOperatorMpesa } = require("../services/platform");
    await updateOperatorMpesa(2, {
      MPESA_CONSUMER_KEY: "",
      MPESA_CONSUMER_SECRET: "",
      MPESA_SHORTCODE: "",
      MPESA_PASSKEY: "",
    }).catch(() => {});
  });
});

describe("stations against the live backend", () => {
  const NAME = `Livetest Site ${Date.now().toString().slice(-6)}`;
  let created;

  test("an operator can add a site to their existing account", async () => {
    authAs(adminTokens);
    const { createStation, fetchStations } = require("../services/routers");

    created = await createStation({ name: NAME, code: "LT" });
    expect(created.id).toBeGreaterThan(0);
    // A station is grouping only — no separate account, no separate billing.
    expect(created).not.toHaveProperty("tenant_subscription");

    const all = await fetchStations();
    expect(all.map((s) => s.name)).toContain(NAME);
  }, 40000);

  test("a duplicate name is a field error, not a crash", async () => {
    authAs(adminTokens);
    const { createStation } = require("../services/routers");
    await expect(createStation({ name: NAME })).rejects.toMatchObject({
      response: { status: 400 },
    });
  }, 40000);

  test("the other operator never sees it", async () => {
    authAs(adminTokens);
    const { fetchStations } = require("../services/routers");
    const mine = await fetchStations();
    expect(mine.map((s) => s.name)).toContain(NAME);

    // blueadmin belongs to the second operator.
    const blue = await tokenFor("blueadmin");
    const theirs = await axios.get(`${API}stations/`, {
      headers: { Authorization: `Bearer ${blue.access}` },
    });
    const names = (theirs.data.results ?? theirs.data).map((s) => s.name);
    expect(names).not.toContain(NAME);
  }, 60000);

  test("an empty station can be removed again", async () => {
    authAs(adminTokens);
    const { deleteStation, fetchStations } = require("../services/routers");
    await deleteStation(created.id);
    const all = await fetchStations();
    expect(all.map((s) => s.name)).not.toContain(NAME);
  }, 40000);
});

describe("analytics against the live backend", () => {
  test("every day in the window is present, in order", async () => {
    authAs(ownerTokens);
    const { fetchPlatformAnalytics } = require("../services/platform");
    const data = await fetchPlatformAnalytics({ days: 14 });

    expect(data.days).toBe(14);
    // Gap-filled: a missing day would render as a fall to zero, which is a
    // different claim from "nothing happened that day".
    expect(data.series).toHaveLength(14);
    const days = data.series.map((p) => p.day);
    expect(days).toEqual([...days].sort());
    expect(new Set(days).size).toBe(14);
  }, 40000);

  test("the operator line only ever goes up", async () => {
    authAs(ownerTokens);
    const { fetchPlatformAnalytics } = require("../services/platform");
    const data = await fetchPlatformAnalytics({ days: 30 });
    const counts = data.series.map((p) => p.operators);
    expect(counts).toEqual([...counts].sort((a, b) => a - b));
  }, 40000);

  test("narrowing to one operator changes the numbers", async () => {
    authAs(ownerTokens);
    const { fetchPlatformAnalytics } = require("../services/platform");
    const all = await fetchPlatformAnalytics({ days: 60 });
    const one = await fetchPlatformAnalytics({ days: 60, tenant: 1 });

    expect(one.operator).toBeTruthy();
    // Skylink is one of several operators, so its slice cannot exceed the whole.
    expect(one.totals.subscriber_revenue).toBeLessThanOrEqual(
      all.totals.subscriber_revenue
    );
    expect(one.totals.subscriber_revenue).toBeGreaterThan(0);
  }, 40000);

  test("an operator cannot read platform analytics", async () => {
    authAs(adminTokens);
    const { fetchPlatformAnalytics } = require("../services/platform");
    await expect(fetchPlatformAnalytics({ days: 7 })).rejects.toMatchObject({
      response: { status: 403 },
    });
  }, 40000);
});

describe("monitoring against the live backend", () => {
  test("platform health names the operator behind each problem", async () => {
    authAs(ownerTokens);
    const { fetchPlatformHealth } = require("../services/platform");
    const health = await fetchPlatformHealth();

    expect(health).toHaveProperty("routers_offline");
    expect(health).toHaveProperty("payments_unconfigured");
    expect(health).toHaveProperty("operators_owing");
    expect(health).toHaveProperty("all_clear");

    // Every entry must be attributable — a problem you cannot pin to an
    // operator is not actionable on this side.
    for (const group of [
      health.routers_offline,
      health.payments_unconfigured,
      health.operators_owing,
    ]) {
      for (const row of group) {
        expect(row.operator).toBeTruthy();
        expect(typeof row.operator_id).toBe("number");
      }
    }
  }, 40000);

  test("an operator cannot see platform health", async () => {
    authAs(adminTokens);
    const { fetchPlatformHealth } = require("../services/platform");
    await expect(fetchPlatformHealth()).rejects.toMatchObject({
      response: { status: 403 },
    });
  }, 40000);

  test("router events carry availability and stay scoped", async () => {
    authAs(adminTokens);
    const { fetchRouterEvents } = require("../services/routers");
    const data = await fetchRouterEvents(7);

    expect(data.days).toBe(7);
    expect(Array.isArray(data.routers)).toBe(true);
    for (const r of data.routers) {
      expect(r.availability).toHaveProperty("uptime_percent");
      expect(r.availability).toHaveProperty("outages");
    }
    // BlueWave's router belongs to the other operator.
    expect(data.routers.map((r) => r.name)).not.toContain("BlueWave Core");
  }, 40000);
});

describe("operator lifecycle against the live backend", () => {
  test("a warning records without changing standing", async () => {
    authAs(ownerTokens);
    const { warnOperator, fetchOperator, fetchOperatorStatusHistory } =
      require("../services/platform");

    const before = await fetchOperator(2);
    const res = await warnOperator(2, "Livetest warning — please ignore");
    expect(res.detail).toMatch(/recorded/i);

    const after = await fetchOperator(2);
    expect(after.status).toBe(before.status);
    expect(after.is_restricted).toBe(before.is_restricted);

    // A warning is a same-status entry, so it shares the timeline with the
    // real transitions rather than living in a separate list.
    const history = await fetchOperatorStatusHistory(2);
    const warned = history.history.find((h) => h.reason?.includes("Livetest warning"));
    expect(warned).toBeTruthy();
    expect(warned.from).toBe(warned.to);
  }, 40000);

  test("an operator admin cannot warn anyone", async () => {
    authAs(adminTokens);
    const { warnOperator } = require("../services/platform");
    await expect(warnOperator(2, "nope")).rejects.toMatchObject({
      response: { status: 403 },
    });
  }, 40000);
});

describe("erasing an operator, against the live backend", () => {
  /**
   * Only the refusals are exercised here. The seeded operators are what every
   * other test in this file reads, so a test that actually deleted one would
   * take the rest of the suite with it — and there is no undo to put it back.
   */
  test("a live operator is refused", async () => {
    authAs(ownerTokens);
    const { deleteOperator, fetchOperator } = require("../services/platform");
    const op = await fetchOperator(2);

    await expect(deleteOperator(2, op.name)).rejects.toMatchObject({
      response: { status: 409 },
    });

    // Still there, and still live.
    expect((await fetchOperator(2)).status).toBe(op.status);
  }, 40000);

  test("an operator admin cannot delete anyone", async () => {
    authAs(adminTokens);
    const { deleteOperator } = require("../services/platform");
    await expect(deleteOperator(2, "Skylink WiFi")).rejects.toMatchObject({
      response: { status: 403 },
    });
  }, 40000);
});

describe("operator picker against the live backend", () => {
  test("lists real operators and requires a reason", async () => {
    authAs(ownerTokens);
    const Modal = require("../components/platform/OperatorPickerModal").default;
    renderWithProviders(<Modal open onClose={() => {}} />, { route: "/platform" });
    await waitFor(() => expect(document.body.textContent).toMatch(/Skylink/i), {
      timeout: 15000,
    });
    // Both seeded operators should be offered.
    expect(document.body.textContent).toMatch(/BlueWave Networks/i);
    expect(document.body.textContent).toMatch(/why are you opening this account/i);
  }, 40000);
});

describe("public pages against the live backend", () => {
  test("Login renders", async () => {
    localStorage.clear();
    const Page = require("../pages/Login").default;
    renderWithProviders(<Page />, { route: "/login" });
    await waitFor(() => expect(document.body.textContent.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 800));
  }, 40000);

  test("HotspotPackages renders for a real tenant token", async () => {
    localStorage.clear();
    const Page = require("../pages/hotspot/HotspotPackages").default;
    renderWithProviders(<Page />, {
      route: `/hotspot?t=${TENANT_TOKEN}`,
    });
    await waitFor(() => expect(document.body.textContent.length).toBeGreaterThan(0), {
      timeout: 15000,
    });
    await new Promise((r) => setTimeout(r, 1500));
    const fatal = consoleErrors.filter(
      (e) => !e.includes("not wrapped in act") && !e.includes("React Router Future Flag")
    );
    if (fatal.length) throw new Error(`HotspotPackages logged errors:\n${fatal.join("\n---\n")}`);
  }, 40000);
});

describe("platform pages against the live backend", () => {
  PLATFORM_PAGES.forEach(([name, load, mustContain]) => {
    test(`${name} renders`, async () => {
      await smoke(name, load, ownerTokens, mustContain);
    }, 40000);
  });
});

describe("operator pages against the live backend", () => {
  ADMIN_PAGES.forEach(([name, load, mustContain]) => {
    test(`${name} renders`, async () => {
      await smoke(name, load, adminTokens, mustContain);
    }, 40000);
  });
});
