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


/* ═══════════════════════════════════════════════════════════════════════
   LANGUAGE

   English only was a decision nobody made — the first person to write a
   string chose, and every string after followed. For most of the people
   standing at a hotspot in this market, Swahili is the language they read
   without effort.

   The choice sticks, because somebody who switched once should not switch
   again on every page.
   ═══════════════════════════════════════════════════════════════════════ */

var STRINGS = {
  en: {
    'brand.tagline':    'Fast, reliable internet access',
    'code.placeholder': 'Code or M-Pesa message',
    'code.connect':     'Connect',
    'code.hint':        "Already paid? Paste the whole M-Pesa message — we'll find it.",
    'code.empty':       'Paste your M-Pesa message, or enter your code.',
    'code.checking':    'Connecting you now…',
    'code.nomatch':     "We couldn't match that. Check you pasted the whole message.",
    'packages.title':   'Buy a package',
    'packages.loading': 'Loading packages…',
    'packages.none':    'No packages are on sale right now. Please ask at the counter.',
    'packages.failed':  "Couldn't load the packages. ",
    'packages.retry':   'Try again',
    'buy':              'Buy',
    'pay.label':        'Your M-Pesa number',
    'pay.submit':       'Submit',
    'pay.note':         'You will get a prompt on this number. Approve it to get online.',
    'pay.badnumber':    'That does not look like a Safaricom number.',
    'pay.sending':      'Sending the prompt…',
    'pay.approve':      'Check your phone and approve the M-Pesa prompt.',
    'pay.paid':         'Paid. Connecting you now…',
    'pay.expired':      'No payment received. The prompt may have expired — you have not been charged.',
    'pay.lost':         'We lost track of that payment. Please try again.',
    'pay.failed':       'Could not start the payment. Please try again.',
    'support.title':    'Need help?',
    'terms.before':     'By connecting you agree to the ',
    'terms.link':       'terms of service',
    'connected.title':  "You're connected",
    'connected.sub':    'Your session is active. Enjoy the internet.',
    'connected.code':   'Your access code',
    'connected.copy':   'Copy',
    'connected.copied': 'Copied',
    'connected.hint':   'Write this down. It reconnects this phone until your time runs out — no need to pay again.',
    'connected.session':'Session',
    'connected.status': 'Status',
    'connected.online': 'Online',
    'connected.package':'Package',
    'connected.left':   'Time left',
    'connected.ip':     'IP address',
    'connected.device': 'Device',
    'connected.used':   'Data used',
    'connected.browse': 'Start browsing',
    'connected.logout': 'Disconnect',
    'connected.details':'Session details',
    'unlimited':        'unlimited',
  },
  sw: {
    'brand.tagline':    'Intaneti ya haraka na ya kutegemewa',
    'code.placeholder': 'Msimbo au ujumbe wa M-Pesa',
    'code.connect':     'Unganisha',
    'code.hint':        'Umeshalipa? Bandika ujumbe mzima wa M-Pesa — tutaupata msimbo.',
    'code.empty':       'Bandika ujumbe wako wa M-Pesa, au weka msimbo wako.',
    'code.checking':    'Tunakuunganisha sasa…',
    'code.nomatch':     'Hatukuweza kuupata. Hakikisha umebandika ujumbe mzima.',
    'packages.title':   'Nunua kifurushi',
    'packages.loading': 'Inapakia vifurushi…',
    'packages.none':    'Hakuna vifurushi kwa sasa. Tafadhali uliza kaunta.',
    'packages.failed':  'Imeshindwa kupakia vifurushi. ',
    'packages.retry':   'Jaribu tena',
    'buy':              'Nunua',
    'pay.label':        'Nambari yako ya M-Pesa',
    'pay.submit':       'Tuma',
    'pay.note':         'Utapokea ujumbe kwenye nambari hii. Ikubali ili uunganishwe.',
    'pay.badnumber':    'Hii haionekani kuwa nambari sahihi.',
    'pay.sending':      'Inatuma ombi…',
    'pay.approve':      'Angalia simu yako na ukubali ombi la M-Pesa.',
    'pay.paid':         'Imelipwa. Tunakuunganisha sasa…',
    'pay.expired':      'Hakuna malipo yaliyopokelewa. Ombi huenda limeisha — hujakatwa pesa.',
    'pay.lost':         'Tumeshindwa kufuatilia malipo hayo. Tafadhali jaribu tena.',
    'pay.failed':       'Imeshindwa kuanzisha malipo. Tafadhali jaribu tena.',
    'support.title':    'Unahitaji msaada?',
    'terms.before':     'Kwa kuunganisha unakubali ',
    'terms.link':       'masharti ya huduma',
    'connected.title':  'Umeunganishwa',
    'connected.sub':    'Kipindi chako kinaendelea. Furahia intaneti.',
    'connected.code':   'Msimbo wako',
    'connected.copy':   'Nakili',
    'connected.copied': 'Imenakiliwa',
    'connected.hint':   'Andika msimbo huu. Utakuunganisha tena kwa simu hii hadi muda wako uishe — bila kulipa tena.',
    'connected.session':'Kipindi',
    'connected.status': 'Hali',
    'connected.online': 'Mtandaoni',
    'connected.package':'Kifurushi',
    'connected.left':   'Muda uliobaki',
    'connected.ip':     'Anwani ya IP',
    'connected.device': 'Kifaa',
    'connected.used':   'Data iliyotumika',
    'connected.browse': 'Anza kuvinjari',
    'connected.logout': 'Ondoka',
    'connected.details':'Maelezo ya kipindi',
    'unlimited':        'bila kikomo',
  }
};

var LANG = (function () {
  try {
    var saved = window.localStorage.getItem('portal_lang');
    if (saved && STRINGS[saved]) { return saved; }
  } catch (e) { /* a captive browser may refuse storage */ }
  // What the phone is already set to, if we speak it.
  var nav = (navigator.language || 'en').slice(0, 2).toLowerCase();
  return STRINGS[nav] ? nav : 'en';
})();

function t(key) {
  return (STRINGS[LANG] && STRINGS[LANG][key]) || STRINGS.en[key] || key;
}

/** Replace the text of anything carrying a data-i18n key. */
function applyLanguage() {
  var nodes = document.querySelectorAll('[data-i18n]');
  for (var i = 0; i < nodes.length; i++) {
    nodes[i].textContent = t(nodes[i].getAttribute('data-i18n'));
  }
  var holders = document.querySelectorAll('[data-i18n-placeholder]');
  for (var j = 0; j < holders.length; j++) {
    holders[j].placeholder = t(holders[j].getAttribute('data-i18n-placeholder'));
  }
  document.documentElement.setAttribute('lang', LANG);
}

function setLanguage(lang) {
  if (!STRINGS[lang]) { return; }
  LANG = lang;
  try { window.localStorage.setItem('portal_lang', lang); } catch (e) { /* fine */ }
  applyLanguage();
  var toggle = document.getElementById('langToggle');
  if (toggle) { toggle.textContent = lang === 'en' ? 'Kiswahili' : 'English'; }
}

/** Wire the toggle and paint the page in whichever language was chosen. */
function initLanguage() {
  var toggle = document.getElementById('langToggle');
  if (toggle) {
    toggle.textContent = LANG === 'en' ? 'Kiswahili' : 'English';
    toggle.addEventListener('click', function () {
      setLanguage(LANG === 'en' ? 'sw' : 'en');
    });
  }
  applyLanguage();
}

/* ═══════════════════════════════════════════════════════════════════════
   DEVICE TOKEN

   Proof that this phone belongs to the account, rather than merely knowing
   a MAC address. The backend hands one back when a code is redeemed or a
   router lets a bound device back on, and asks for it before returning the
   access code from /hotspot/status/ — because everyone else's MAC on a
   shared hotspot is a scanner app away, and the endpoint cannot tell who
   is really calling.

   Kept per MAC: a phone that changes address, or a laptop that has been on
   two accounts, should not present the wrong one. Storage can be refused
   (private browsing, a locked-down WebView), and that is survivable — the
   page then shows the package and time left without the code.
   ═══════════════════════════════════════════════════════════════════════ */

function deviceTokenKey(mac) {
  return 'portal_dt_' + String(mac || '').toUpperCase();
}

function saveDeviceToken(mac, token) {
  if (!mac || !token) { return; }
  try { window.localStorage.setItem(deviceTokenKey(mac), token); } catch (e) { /* fine */ }
}

function loadDeviceToken(mac) {
  if (!mac) { return ''; }
  try { return window.localStorage.getItem(deviceTokenKey(mac)) || ''; } catch (e) { return ''; }
}

/** Bytes as something a person reads. */
function humanBytes(bytes) {
  var n = Number(bytes) || 0;
  if (n >= 1024 * 1024 * 1024) { return (n / 1073741824).toFixed(2) + ' GB'; }
  if (n >= 1024 * 1024) { return (n / 1048576).toFixed(1) + ' MB'; }
  return Math.round(n / 1024) + ' KB';
}
