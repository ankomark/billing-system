"""
The page a device behind somebody's phone hotspot is sent to.

The router recognises it by its hop counter and NATs its plain-HTTP traffic
here (services/tethering.py, redirect_rules). Most of the time the request is
the phone's own "is there internet?" check, so this opens in the "Sign in to
network" sheet, and its whole job is to turn a person getting internet for
free into a person buying their own.

It cannot sell them anything where they stand. A purchase binds to the device
the router sees, and the router sees the phone they are tethered to -- buying
here would put the bundle on somebody else's handset. So it says what to do:
leave the hotspot, join the WiFi, buy there.

Plain HTML, no scripts, no assets: it is shown in a captive-portal sheet on
whatever handset, sometimes over a connection that can only reach this one
host.
"""

from django.http import HttpResponse

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Get your own bundle</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5b6270;--accent:#0b6bcb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--ink:#eef0f4;--muted:#a3a9b5;--accent:#5aa8ff}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:440px;margin:0 auto;padding:28px 16px}
.card{background:var(--card);border-radius:14px;padding:22px 20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h1{font-size:1.35rem;margin:0 0 10px}
p{margin:0 0 14px;color:var(--muted)}
ol{margin:0 0 16px;padding-left:22px}li{margin:6px 0}
.sw{border-top:1px solid rgba(127,127,127,.25);margin-top:18px;padding-top:14px;font-size:.95rem}
b{color:var(--ink)}
</style></head><body><main><div class="card">
<h1>This connection is shared from another phone</h1>
<p>You're connected to somebody's phone hotspot. Internet from this WiFi
service can't be shared that way.</p>
<p><b>Get your own bundle in a minute:</b></p>
<ol>
<li>Disconnect from this hotspot.</li>
<li>Join the WiFi network directly from your phone's WiFi list.</li>
<li>The login page opens: pick a package and pay with M-Pesa.</li>
</ol>
<div class="sw"><p><b>Muunganisho huu unashirikiwa kutoka simu nyingine.</b>
Ondoka kwenye hotspot hii, unganisha moja kwa moja kwenye WiFi, kisha
nunua kifurushi chako kwa M-Pesa.</p></div>
</div></main></body></html>"""


def shared_connection(request):
    response = HttpResponse(PAGE)
    # A captive sheet that caches this would show it again after the person
    # has done as asked and bought a bundle of their own.
    response["Cache-Control"] = "no-store"
    return response
