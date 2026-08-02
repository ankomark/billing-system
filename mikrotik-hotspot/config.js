/* ═══════════════════════════════════════════════════════════════════════
   EDIT THIS FILE — and only this one.

   Every page in this folder reads its settings from here. They each carried
   their own copy before, which meant four places to keep in step by hand and
   nothing to notice when they drifted apart.

   Upload the whole folder to the router's hotspot directory:
       /files  →  drag the folder in, or  ftp  to hotspot/
   ═══════════════════════════════════════════════════════════════════════ */

/* Your backend, with no trailing slash. */
var API_BASE = 'https://your-backend.com/api';

/* Your operator token, from Admin → Settings → your account.

   This is what tells the backend whose portal this is. A device MAC is only
   unique within one operator, so without it the backend cannot tell whose
   subscriber is connecting and refuses rather than guessing.

   It is also where your business name, packages and support numbers come
   from — change them in your settings and these pages follow. Nothing about
   your branding is edited here. */
var TENANT_TOKEN = 'YOUR-OPERATOR-TOKEN';

/* ═══════════════════════════════════════════════════════════════════════ */

/**
 * Put the operator's name on a page.
 *
 * These files sit on a router and cannot know whose they are without asking.
 * They used to carry a name hardcoded — one operator's business, in a template
 * every operator deploys — and after that was removed they carried nothing,
 * which left a subscriber looking at the billing platform's branding instead
 * of the provider they actually pay.
 *
 * Quiet on failure: a page that cannot reach the backend still shows a session
 * and still works, and a missing name is not worth an error over.
 */
function loadProviderName(onName) {
  if (!API_BASE || API_BASE.indexOf('your-backend') !== -1) { return; }

  var xhr = new XMLHttpRequest();
  xhr.open('GET', API_BASE + '/hotspot/provider/?t=' + encodeURIComponent(TENANT_TOKEN), true);
  xhr.timeout = 8000;
  xhr.onload = function () {
    if (xhr.status !== 200) { return; }
    try {
      var data = JSON.parse(xhr.responseText);
      if (data && data.provider) { onName(data.provider, data.support_phones || []); }
    } catch (e) { /* nothing worth saying */ }
  };
  xhr.onerror = xhr.ontimeout = function () { /* nothing worth saying */ };
  xhr.send();
}

/**
 * Write a name into an element and the tab title, as text.
 *
 * An operator types their own business name, and these pages have no framework
 * escaping anything.
 */
function applyProviderName(elementId, name) {
  var el = document.getElementById(elementId);
  if (el) { el.textContent = name; }
  document.title = name;
}
