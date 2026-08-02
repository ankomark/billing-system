import fs from "fs";
import path from "path";

/**
 * The things that break the operator console on a phone.
 *
 * These read the source rather than render it, because the failures are
 * structural and a jsdom test at one width would not show them: jsdom has no
 * layout engine, so an overflowing table looks identical to a fitting one.
 *
 * Kept narrow on purpose — only the mistakes that make something unusable,
 * not a judgement about how anything looks.
 */

const SRC = path.join(__dirname, "..");
const AREAS = ["pages/admin", "components/admin", "pages/platform", "pages/customer"];

function jsxFiles() {
  const found = [];
  const walk = (dir) => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".jsx")) found.push(full);
    }
  };
  AREAS.forEach((a) => walk(path.join(SRC, a)));
  return found;
}

const files = jsxFiles();

describe("responsive structure", () => {
  test("there are files to check", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  /**
   * A table wider than the screen inside an overflow-hidden card is not
   * merely awkward — the right-hand columns are clipped and cannot be
   * reached at all. Routers and Users both shipped that way.
   */
  test("every table can be scrolled sideways", () => {
    const offenders = [];

    for (const file of files) {
      const text = fs.readFileSync(file, "utf8");
      let index = text.indexOf("<table");
      while (index !== -1) {
        const above = text.slice(Math.max(0, index - 600), index);
        if (!/overflow-x-auto|overflow-auto/.test(above)) {
          const line = text.slice(0, index).split("\n").length;
          offenders.push(`${path.relative(SRC, file)}:${line}`);
        }
        index = text.indexOf("<table", index + 1);
      }
    }

    expect(offenders).toEqual([]);
  });

  /**
   * A value with nothing to break on — a PPPoE username, a MAC, an email —
   * in a narrow column runs past the edge of its card and takes the page
   * width with it. These two render exactly that kind of value.
   */
  test("label and value pairs can wrap", () => {
    const rowComponents = [
      ["pages/admin/CustomerDetail.jsx", "function InfoRow"],
      ["pages/admin/MyPlatformAccount.jsx", "function Row"],
    ];

    for (const [file, marker] of rowComponents) {
      const full = path.join(SRC, file);
      if (!fs.existsSync(full)) continue;
      const text = fs.readFileSync(full, "utf8");
      const start = text.indexOf(marker);
      expect(start).toBeGreaterThan(-1);
      const body = text.slice(start, start + 600);
      expect(body).toMatch(/break-words|truncate|break-all/);
    }
  });

  /**
   * The console is reached from a phone, so the navigation has to be. A
   * sidebar with no drawer is a 240px column permanently eating a 360px
   * screen.
   */
  test("the sidebar collapses and can be opened", () => {
    const sidebar = fs.readFileSync(
      path.join(SRC, "components/admin/AdminSidebar.jsx"), "utf8");
    expect(sidebar).toMatch(/-translate-x-full/);   // off-screen by default
    expect(sidebar).toMatch(/lg:translate-x-0/);    // and back on a laptop

    const layout = fs.readFileSync(
      path.join(SRC, "components/admin/AdminLayout.jsx"), "utf8");
    expect(layout).toMatch(/lg:hidden/);            // a way to open it
  });

  /**
   * The page body must never scroll sideways. Wide content scrolls inside its
   * own container instead, which is what the table rule above is about.
   */
  test("no page forces the whole body wider than the screen", () => {
    const offenders = [];
    for (const file of files) {
      const text = fs.readFileSync(file, "utf8");
      // A fixed width larger than a small phone, on anything that is not
      // explicitly capped by a max-width.
      const matches = text.match(/\bw-\[(\d{3,})px\]/g) || [];
      for (const m of matches) {
        const px = Number(m.match(/(\d+)/)[1]);
        if (px > 420) offenders.push(`${path.relative(SRC, file)} ${m}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
