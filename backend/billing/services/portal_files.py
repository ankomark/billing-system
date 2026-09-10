"""
Putting the captive-portal files on a router, over the tunnel.

Until now this was WinBox and a drag, once per router, per change -- so the
files drift. On 2026-09-10 both live routers were still serving a login.html
from before a fix made two days earlier, and nobody knew, because nothing
compares them.

Three things make this safe enough to automate.

**The routers only accept tcp/8728 from the tunnel**, which is right and stays
right. FTP is opened for the platform server's tunnel address alone
(src=10.10.0.1/32 on the tunnel interface) and closed again in a finally, so it
comes out even when the upload throws. The server already holds the router's
admin password, so this exposes nothing it did not already have.

**config.js is per-router.** The copy in the repository carries placeholders
for the API address, the operator token and the router token; uploading it
unchanged leaves the portal identifying itself to nobody, which is worse than
leaving it alone. It is rebuilt here from the template.

**And the rebuild is proved, not assumed.** A first attempt at this compared
the two files with a hand-rolled heuristic, which also stripped continuation
lines from the original and refused a perfectly correct upload. What the check
asks now is exact: does the router's copy contain any line the rebuild does
not? If not, the rebuild is a strict superset -- everything on that router
survives and the only change is what we are adding. Anything else and it
refuses rather than clobber a file somebody edited by hand.
"""
import difflib
import io
import logging
import os
from ftplib import FTP
from pathlib import Path

logger = logging.getLogger(__name__)

# The rule is tagged so it can be found and removed again without guessing,
# and so a leftover is obvious to anyone reading the firewall.
RULE_TAG = "TEMP | WIFI BILLING SYSTEM | portal upload"

# Only config.js is per-router. The rest are the same bytes everywhere, which
# is why they can be compared by size and skipped when they already match.
TEMPLATED = "config.js"

PLACEHOLDERS = {
    "https://your-backend.com/api": "api_base",
    "YOUR-OPERATOR-TOKEN": "tenant_token",
    "YOUR-ROUTER-TOKEN": "router_token",
}


def portal_dir():
    """
    Where the portal files are, in the container or in a checkout.

    The folder sits at the repository root, outside the Docker build context,
    so it is mounted rather than copied. Both are checked for the same reason
    MikrotikPortalPageTests checks both.
    """
    from django.conf import settings

    root = Path(settings.BASE_DIR).parent
    for candidate in (Path("/mikrotik-hotspot"), root / "mikrotik-hotspot"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("no mikrotik-hotspot directory found")


def build_config(router, api_base, template=None):
    """config.js for one router, with the three placeholders filled in."""
    if template is None:
        template = io.open(portal_dir() / TEMPLATED, encoding="utf-8").read()

    values = {
        "api_base": api_base,
        "tenant_token": router.tenant.public_token or "",
        "router_token": router.public_token or "",
    }

    # Checked before substituting, not after.
    #
    # Replacing a placeholder with an empty string removes it, so a check for
    # surviving placeholders passes and the file ships with an empty token —
    # which is the very state this refuses to upload. The values are what
    # matter; the placeholders are only where they go.
    empty = sorted(k for k, v in values.items() if not str(v).strip())
    if empty:
        raise ValueError(
            f"nothing to substitute for {empty} on {router} — a portal with an "
            f"empty token identifies itself to nobody, and _portal_router then "
            f"falls back to selection by load")

    built = template
    for placeholder, key in PLACEHOLDERS.items():
        built = built.replace(placeholder, values[key])

    left = [p for p in PLACEHOLDERS if p in built]
    if left:
        # Belt and braces: a template whose placeholder spelling has drifted
        # would otherwise upload unsubstituted.
        raise ValueError(f"placeholders still present after substitution: {left}")
    return built


def lost_lines(old, new):
    """
    Lines the router has that the replacement does not. Empty means safe.

    Exact, deliberately. The heuristic this replaced compared the two files
    with the new lines filtered out by pattern, and the patterns also matched
    continuation lines in the original — so a correct substitution was refused
    and the upload did not happen.
    """
    a = [l.rstrip("\r") for l in (old or "").splitlines()]
    b = [l.rstrip("\r") for l in (new or "").splitlines()]
    return [d[1:] for d in difflib.unified_diff(a, b, lineterm="", n=0)
            if d.startswith("-") and not d.startswith("---")]


def compare(router, api, api_base, names):
    """
    What differs, read over the API alone.

    Deliberately does not use FTP. Reading the files that way would mean
    opening the firewall for a command whose whole purpose is to look and not
    touch, and /file already carries a size for everything on the router —
    which is enough to answer "is this the build we have". The byte-exact
    check happens in push(), where the file has to be fetched anyway to prove
    the replacement loses nothing.

    Returns (name, on_router, ours, action) with action one of "same",
    "differs", "absent" (not on the router yet) or "missing" (not in our
    folder).
    """
    folder = portal_dir()
    sizes = {}
    for f in api.path("file"):
        name = str(f.get("name") or "")
        if name.startswith("hotspot/"):
            sizes[name[len("hotspot/"):]] = int(f.get("size") or 0)

    results = []
    for name in names:
        src = folder / name
        if not src.is_file():
            results.append((name, sizes.get(name), None, "missing"))
            continue

        if name == TEMPLATED:
            ours = len(build_config(router, api_base).encode("utf-8"))
        else:
            ours = src.stat().st_size

        on_router = sizes.get(name)
        if on_router is None:
            results.append((name, None, ours, "absent"))
        elif on_router == ours:
            results.append((name, on_router, ours, "same"))
        else:
            results.append((name, on_router, ours, "differs"))
    return results


def _open_ftp(router, api, tunnel_ip, iface):
    """Open FTP for the tunnel server only. Returns the rule id to remove."""
    fw = api.path("ip", "firewall", "filter")
    fw.add(chain="input", action="accept",
           **{"in-interface": iface,
              "src-address": f"{tunnel_ip}/32",
              "protocol": "tcp",
              "place-before": "0"},
           comment=RULE_TAG)
    return next((f[".id"] for f in api.path("ip", "firewall", "filter")
                 if str(f.get("comment")) == RULE_TAG), None)


def _close_ftp(api, rule_id):
    if not rule_id:
        return
    try:
        api.path("ip", "firewall", "filter").remove(rule_id)
    except Exception:
        # Loud, because a rule left behind is a port left open. It is scoped to
        # one address on the tunnel, so this is untidy rather than dangerous,
        # but it must not pass unremarked.
        logger.exception("[portal] could not remove temporary rule %s", rule_id)


def push(router, api, api_base, tunnel_ip, iface, names, backup_dir=None,
         apply=False):
    """
    Compare, and optionally upload, the portal files on one router.

    Returns a list of (name, before, after, action) — action being one of
    "same", "would-update", "updated", "refused" or "missing".
    """
    folder = portal_dir()
    results = []
    rule_id = None

    try:
        # Opened whether or not we end up writing: the superset proof needs
        # the router's current copy, and that comes over FTP. compare() is the
        # path that looks without touching the firewall at all.
        rule_id = _open_ftp(router, api, tunnel_ip, iface)

        ftp = FTP()
        ftp.connect(router.ip_address, 21, timeout=30)
        ftp.login(router.username, router.password)
        try:
            for name in names:
                src = folder / name
                if not src.is_file():
                    results.append((name, None, None, "missing"))
                    continue

                if name == TEMPLATED:
                    new = build_config(router, api_base).encode("utf-8")
                else:
                    new = src.read_bytes()

                buf = io.BytesIO()
                try:
                    ftp.retrbinary(f"RETR hotspot/{name}", buf.write)
                    old = buf.getvalue()
                except Exception:
                    old = b""

                if backup_dir and old:
                    os.makedirs(backup_dir, exist_ok=True)
                    with open(os.path.join(backup_dir,
                                           f"{router.name}-{name}"), "wb") as fh:
                        fh.write(old)

                if old == new:
                    results.append((name, len(old), len(new), "same"))
                    continue

                if name == TEMPLATED:
                    lost = lost_lines(old.decode("utf-8", "replace"),
                                      new.decode("utf-8", "replace"))
                    if lost:
                        results.append((name, len(old), len(new), "refused"))
                        logger.warning(
                            "[portal] %s on %s has %d line(s) the rebuild "
                            "lacks — not overwritten", name, router, len(lost))
                        continue

                if not apply:
                    results.append((name, len(old), len(new), "would-update"))
                    continue

                ftp.storbinary(f"STOR hotspot/{name}", io.BytesIO(new))
                results.append((name, len(old), len(new), "updated"))
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
    finally:
        _close_ftp(api, rule_id)

    return results


def leftover_rules(api):
    """Any temporary rule this module failed to clean up."""
    return [f for f in api.path("ip", "firewall", "filter")
            if str(f.get("comment")) == RULE_TAG]
