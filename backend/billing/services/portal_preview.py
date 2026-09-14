"""
The captive portal, rendered for an operator looking at it from the console.

An operator has never been able to see their own portal without walking to a
router, joining the WiFi on a phone and getting themselves disconnected first.
So the things that are easy to get wrong there — a notice long enough to push
the packages off the screen, a package name that reads badly at half width, a
featured package nobody set — are found by customers rather than by the person
who can fix them.

This serves the REAL login.html, not a copy of it. A rebuilt preview in the
console would drift from the file on the routers within a month, and a preview
that does not match what customers see is worse than none: it invites an
operator to sign off on a screen nobody has.

Two things have to be done to that file before a browser outside a router can
render it.

**The MikroTik substitutions.** RouterOS replaces $(mac), $(chap-id) and the
rest before it serves the page. Nothing does that here, so they would arrive
as literal text — and the $(if error) block would render its error markup
unconditionally. They are filled with harmless stand-ins instead.

**config.js.** The copy in the repository carries placeholders; the copy on a
router carries that router's tokens. It is rebuilt here exactly as the upload
rebuilds it, so the preview talks to the same backend with the same operator
token and shows the operator's own live packages, notice and featured choice.

What is NOT done here is making the page safe to poke at. Connect redeems a
code against a MAC and Buy ends in an M-Pesa prompt on somebody's phone, and
both are refused by a guard inside login.html itself — see PREVIEW there. A
guard in this file would be one redesign of the portal away from being
forgotten, and the failure would be an operator charging a stranger to find
out what a button did.
"""

import io
import re

# What RouterOS would have substituted. Every one of these is inert: the MAC is
# from the documentation range, the CHAP values are empty so the login path
# falls back rather than computing a hash over nonsense, and the links point
# nowhere a click can reach.
SUBSTITUTIONS = {
    "$(mac)": "02:00:00:00:00:01",
    "$(ip)": "192.168.88.100",
    "$(username)": "",
    "$(password)": "",
    "$(chap-id)": "",
    "$(chap-challenge)": "",
    "$(link-login)": "#",
    "$(link-login-only)": "#",
    "$(link-logout)": "#",
    "$(link-status)": "#",
    "$(link-orig)": "#",
    "$(link-orig-esc)": "#",
    "$(link-redirect)": "#",
    "$(hostname)": "preview",
    "$(identity)": "preview",
    "$(session-time-left)": "",
    "$(uptime)": "",
    "$(error)": "",
}

# $(if error) ... $(endif) wraps the router's own error message. There is no
# error in a preview, so the block is dropped whole rather than left to render
# an empty red box the operator would reasonably ask about.
IF_BLOCK = re.compile(r"\$\(if\s+\w+\)(.*?)\$\(endif\)", re.S)


def render(tenant, api_base, router=None):
    """
    The portal as HTML, self-contained, for one operator.

    `router` picks whose router token the page carries. Any of the operator's
    will do — the token decides which station a purchase is built at, and
    nothing is purchased here — so the caller may pass none and get the first.

    Returns a single document with config.js and md5.js inlined, because the
    preview is served from one endpoint and a browser asking for neighbouring
    files would get the API's 404 rather than a script.
    """
    from billing.services.portal_files import build_config, portal_dir

    folder = portal_dir()
    html = io.open(folder / "login.html", encoding="utf-8").read()

    if router is None:
        from billing.models import RouterDevice

        # all_tenants, then filtered: the tenant FK carries related_name="+",
        # so there is no reverse accessor to go through.
        router = (RouterDevice.objects.all_tenants()
                  .filter(tenant=tenant, is_active=True)
                  .order_by("id").first())
    if router is None:
        raise ValueError(
            f"{tenant} has no active router, so there is no router token to "
            f"build the portal with"
        )

    config = build_config(router, api_base)
    md5 = io.open(folder / "md5.js", encoding="utf-8").read()

    html = IF_BLOCK.sub("", html)
    for token, value in SUBSTITUTIONS.items():
        html = html.replace(token, value)

    # Inlined in place of the tags that fetch them. Ordered as the page orders
    # them, because config.js defines what everything below it reads.
    html = html.replace(
        '<script src="config.js"></script>',
        "<script>\n" + config + "\n"
        # The flag the guard in login.html reads. Declared after config.js so
        # a config that happens to define it cannot win.
        + "\nvar PORTAL_PREVIEW = true;\n</script>",
        1,
    )
    html = html.replace(
        '<script src="md5.js"></script>',
        "<script>\n" + md5 + "\n</script>",
        1,
    )

    return html
