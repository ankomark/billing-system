import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import AdminLayout from "../../components/admin/AdminLayout";
import { Skeleton } from "../../components/ui/Skeleton";
import {
  fetchSystemSettings,
  updateSystemSettings,
  testMpesa,
  testSms,
  fetchSmsBalance,
  testWhatsapp,
} from "../../services/settings";

const EMPTY = {
  MPESA_CONSUMER_KEY: "",
  MPESA_CONSUMER_SECRET: "",
  MPESA_SHORTCODE: "",
  MPESA_PASSKEY: "",
  MPESA_CALLBACK_URL: "",
  BLESSEDTEXTS_API_KEY: "",
  BLESSEDTEXTS_SENDER_ID: "",
  WHATSAPP_TOKEN: "",
  WHATSAPP_PHONE_ID: "",
  SUPPORT_PHONE: "",
  SUPPORT_PHONE_2: "",
  HOTSPOT_TERMS_URL: "",
  SMS_TEMPLATE_VOUCHER: "",
  SMS_TEMPLATE_PPPOE: "",
  SMS_TEMPLATE_WELCOME_HOTSPOT: "",
  SMS_TEMPLATE_WELCOME_PPPOE: "",
};

/**
 * GSM 03.38, and what a message costs in it.
 *
 * The same table as billing/message_templates.py, which is the one that
 * decides whether a template may be saved — this copy only draws the counter
 * as somebody types. Frozen by the standard, so it does not drift.
 *
 * The rule worth knowing: every character here is one of 160 in a part. One
 * character that is not switches the whole message to UCS-2 and a part becomes
 * 70, which is how a voucher SMS came to cost three parts over a single em
 * dash.
 */
const GSM_BASIC = new Set(
  "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?" +
  "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
);
const GSM_EXTENDED = new Set("^{}\\[~]|€");

/**
 * A template with stand-in values in it, the way the backend renders one.
 *
 * A line whose only placeholders are empty is dropped whole, matching _fill in
 * message_templates.py — so "Help: {support}" with no support number shows as
 * nothing rather than as a dangling label.
 */
function fillSample(template, sample) {
  return (template || "")
    .split("\n")
    .filter((line) => {
      const used = [...line.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
      return !used.length || !used.every((n) => !String(sample[n] ?? "").trim());
    })
    .map((line) => line.replace(/\{(\w+)\}/g, (whole, n) => sample[n] ?? whole))
    .join("\n");
}

function smsParts(text) {
  const chars = [...(text || "")];
  const offenders = [...new Set(
    chars.filter((c) => !GSM_BASIC.has(c) && !GSM_EXTENDED.has(c))
  )];

  let length;
  let single;
  let multi;
  if (offenders.length) {
    length = chars.length;
    [single, multi] = [70, 67];
  } else {
    length = chars.reduce((n, c) => n + (GSM_EXTENDED.has(c) ? 2 : 1), 0);
    [single, multi] = [160, 153];
  }

  const parts = length === 0 ? 0 : length <= single ? 1 : Math.ceil(length / multi);
  return { length, parts, unicode: offenders.length > 0, offenders };
}

export default function SystemSettings() {
  const qc = useQueryClient();
  const [form, setForm]     = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState("");
  // Per-field, because the server refuses a template for a specific reason —
  // a missing {voucher}, a mistyped placeholder — and a toast that says
  // "failed to save settings" throws that reason away.
  const [fieldErrors, setFieldErrors] = useState({});

  const { data: settings, isLoading } = useQuery({
    queryKey: ["system-settings"],
    queryFn: fetchSystemSettings,
    staleTime: 5 * 60 * 1000,
  });

  // Read on its own so a provider that is slow or unreachable delays this
  // panel rather than the whole settings page.
  const { data: smsBalance, refetch: refetchBalance } = useQuery({
    queryKey: ["sms-balance"],
    queryFn: fetchSmsBalance,
    staleTime: 60 * 1000,
    retry: false,
  });

  useEffect(() => {
    if (settings) setForm({ ...EMPTY, ...settings });
  }, [settings]);

  const handleChange = (e) => {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setFieldErrors({});
    try {
      await updateSystemSettings(form);
      toast.success("Settings saved successfully");
      qc.invalidateQueries({ queryKey: ["system-settings"] });
    } catch (err) {
      const data = err.response?.data;
      // DRF answers a failed validation with {field: [reason]}. Keep those
      // against their fields — the reason is the whole value of the check.
      const perField = {};
      if (data && typeof data === "object" && !data.detail) {
        for (const [key, value] of Object.entries(data)) {
          if (key in EMPTY) perField[key] = Array.isArray(value) ? value[0] : String(value);
        }
      }
      setFieldErrors(perField);
      const first = Object.values(perField)[0];
      toast.error(first || data?.detail || "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (type) => {
    setTesting(type);
    try {
      let res;
      if (type === "mpesa")     res = await testMpesa();
      if (type === "sms")       res = await testSms();
      if (type === "whatsapp")  res = await testWhatsapp();

      if (type === "sms") {
        // This one waits for the provider rather than queueing, so it can say
        // what actually happened.
        refetchBalance();
        toast.success(
          res?.balance != null
            ? `Test SMS sent to ${res.sent_to} · ${res.balance} credit left`
            : `Test SMS sent to ${res.sent_to}`
        );
      } else {
        toast.success(res?.message || "Connection successful");
      }
    } catch (err) {
      toast.error("Test failed: " + (err.response?.data?.error || err.message));
    } finally {
      setTesting("");
    }
  };

  if (isLoading) {
    return (
      <AdminLayout>
        <div className="max-w-3xl space-y-4">
          <Skeleton className="h-8 w-48" />
          <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-6 space-y-4">
            {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        </div>
      </AdminLayout>
    );
  }

  return (
    <AdminLayout>
      <div className="max-w-3xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">System Settings</h1>
          <p className="text-slate-400 text-sm mt-1">
            Configure M-Pesa, SMS, and WhatsApp integrations
          </p>
        </div>

        {/* Read-only identity. The backend has always returned these, with a
            comment saying to surface them here rather than making an operator
            ask — and nothing rendered them, so the token an operator needs for
            their own captive portal could only be read out of the database.
            Absent for platform staff, who have no tenant of their own. */}
        {settings?.TENANT_TOKEN && (
          <Section title="Captive portal setup">
            <Readonly
              label="Business name"
              value={settings.BUSINESS_NAME}
              hint="Shown at the top of the portal your subscribers see."
            />
            <Readonly
              label="Tenant token"
              value={settings.TENANT_TOKEN}
              mono
              hint="Goes in TENANT_TOKEN in config.js on your MikroTik. It identifies whose portal this is — a device MAC is only unique within one operator."
            />
            <Readonly
              label="M-Pesa callback URL"
              value={settings.MPESA_CALLBACK_URL_EFFECTIVE}
              mono
              hint={
                settings.MPESA_CALLBACK_HINT ||
                "Register exactly this with Safaricom. It carries your token, so the callback loads the right operator's credentials."
              }
            />
          </Section>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">

          {/* M-Pesa */}
          <Section title="M-Pesa STK Push">
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Consumer Key"    name="MPESA_CONSUMER_KEY"    value={form.MPESA_CONSUMER_KEY}    onChange={handleChange} />
              <Field label="Consumer Secret" name="MPESA_CONSUMER_SECRET" value={form.MPESA_CONSUMER_SECRET} onChange={handleChange} />
              <Field label="Shortcode"       name="MPESA_SHORTCODE"       value={form.MPESA_SHORTCODE}       onChange={handleChange} placeholder="e.g. 174379" />
              <Field label="Passkey"         name="MPESA_PASSKEY"         value={form.MPESA_PASSKEY}         onChange={handleChange} />
              <div className="sm:col-span-2">
                <Field label="Callback URL"  name="MPESA_CALLBACK_URL"    value={form.MPESA_CALLBACK_URL}    onChange={handleChange} placeholder="https://yourdomain.com/api/mpesa/stk-callback/" />
              </div>
            </div>
            <TestBtn label="Test M-Pesa" color="emerald" loading={testing === "mpesa"} onClick={() => runTest("mpesa")} />
          </Section>

          {/* BlessedTexts */}
          <Section title="BlessedTexts (SMS)">
            <div className="grid sm:grid-cols-2 gap-4">
              <Field
                label="API Key"
                name="BLESSEDTEXTS_API_KEY"
                value={form.BLESSEDTEXTS_API_KEY}
                onChange={handleChange}
              />
              <Field
                label="Sender ID"
                name="BLESSEDTEXTS_SENDER_ID"
                value={form.BLESSEDTEXTS_SENDER_ID}
                onChange={handleChange}
              />
            </div>
            <p className="text-xs text-slate-500">
              Both from your BlessedTexts profile. The sender ID must be one
              already assigned to your account.
            </p>
            {/* The most common reason messages stop arriving is an empty
                account, and nothing else in the product would show it. */}
            {smsBalance?.ok && (
              <p
                className={`text-xs ${
                  smsBalance.balance != null && smsBalance.balance < 50
                    ? "text-amber-300"
                    : "text-slate-400"
                }`}
              >
                {smsBalance.balance} SMS credit remaining
                {smsBalance.balance != null && smsBalance.balance < 50
                  ? " — top up before it runs out"
                  : ""}
              </p>
            )}
            {smsBalance && !smsBalance.ok && smsBalance.error && (
              <p className="text-xs text-amber-300">
                Couldn't read your balance: {smsBalance.error}
              </p>
            )}
            <TestBtn
              label="Send a test SMS"
              color="amber"
              loading={testing === "sms"}
              onClick={() => runTest("sms")}
            />
          </Section>

          {/* Message wording */}
          <Section title="Message wording">
            <p className="text-sm text-slate-400">
              What your customers actually receive. Leave one blank to use ours.
              The counter is what it costs to send: a message is 160 characters
              per SMS, but one emoji or a dash like — drops that to 70, so a
              short message can quietly cost three times as much.
            </p>
            {Object.entries(settings?.SMS_TEMPLATES || {}).map(([key, spec]) => (
              <TemplateEditor
                key={key}
                name={key}
                spec={spec}
                value={form[key] || ""}
                error={fieldErrors[key]}
                onChange={handleChange}
              />
            ))}
          </Section>

          {/* WhatsApp */}
          <Section title="WhatsApp Cloud API">
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="WhatsApp Token"   name="WHATSAPP_TOKEN"    value={form.WHATSAPP_TOKEN}    onChange={handleChange} />
              <Field label="Phone Number ID"  name="WHATSAPP_PHONE_ID" value={form.WHATSAPP_PHONE_ID} onChange={handleChange} />
            </div>
            <TestBtn label="Test WhatsApp" color="violet" loading={testing === "whatsapp"} onClick={() => runTest("whatsapp")} />
          </Section>

          {/* Terms */}
          <Section title="Terms of service">
            <p className="text-sm text-slate-400">
              Linked at the bottom of your captive portal, where somebody is
              about to connect. Most places expect an internet provider to
              present terms before service is used, and there was nowhere to
              put them.
            </p>
            <Field
              label="Terms page address"
              name="HOTSPOT_TERMS_URL"
              value={form.HOTSPOT_TERMS_URL}
              onChange={handleChange}
              placeholder="https://your-site.com/terms"
            />
            <p className="text-xs text-slate-500">
              Host it where customers can reach it before they are online — the
              same walled garden that serves the portal. Leave it empty and no
              link is shown.
            </p>
          </Section>

          {/* Support numbers */}
          <Section title="Support contacts">
            <p className="text-sm text-slate-400">
              Shown on your captive portal, directly under the packages. A
              customer standing at a hotspot has no internet and no other way to
              reach you — two numbers, because one person is sometimes
              unreachable.
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Support number"
                name="SUPPORT_PHONE"
                value={form.SUPPORT_PHONE}
                onChange={handleChange}
                placeholder="0722 000 000"
              />
              <Field
                label="Second number"
                name="SUPPORT_PHONE_2"
                value={form.SUPPORT_PHONE_2}
                onChange={handleChange}
                placeholder="0733 000 000"
              />
            </div>
          </Section>

          <button
            type="submit"
            disabled={saving}
            className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-6 py-2.5 rounded-lg font-semibold text-sm transition-colors"
          >
            {saving ? "Saving…" : "Save Settings"}
          </button>
        </form>
      </div>
    </AdminLayout>
  );
}

function Section({ title, children }) {
  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/80 shadow-lg shadow-black/20 p-6 space-y-4">
      <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-[0.14em]">{title}</p>
      {children}
    </div>
  );
}

/**
 * A value the operator needs to copy elsewhere, not edit.
 *
 * With a copy button because both of these end up pasted into something
 * unforgiving — a JavaScript file on a router, and Safaricom's callback
 * registration — where one transposed character fails silently rather than
 * loudly. Selecting 32 random characters by hand is where that mistake gets
 * made.
 */
function Readonly({ label, value, hint = "", mono = false }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard needs a secure context and permission. The value is on
      // screen and selectable either way, so a failure here is not worth
      // interrupting anyone over.
    }
  };

  return (
    <div>
      <label className="block text-sm font-medium text-slate-300 mb-1.5">{label}</label>
      <div className="flex gap-2">
        <input
          readOnly
          value={value || ""}
          onFocus={(e) => e.target.select()}
          className={`w-full border border-white/15 bg-slate-950/60 text-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${mono ? "font-mono text-xs" : ""}`}
        />
        <button
          type="button"
          onClick={copy}
          disabled={!value}
          className="shrink-0 border border-white/15 hover:bg-white/5 text-slate-300 px-3 py-2 rounded-lg text-sm disabled:opacity-40 transition-colors"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {hint && <p className="mt-1.5 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

/**
 * One message, with what it will cost to send it.
 *
 * The counter is the point of this. An operator writing their own wording has
 * no way to know that adding an emoji tripled the price of every message they
 * send, and the bill arrives as a balance quietly dropping faster than
 * expected — which is not a thing anybody traces back to a settings page.
 */
function TemplateEditor({ name, spec, value, error, onChange }) {
  // What they will actually send: their wording where they have written some,
  // and ours where the box is empty, since empty means ours.
  const effective = value.trim() ? value : spec.default;

  // Counted on the message, not the template. {expiry} is eight characters
  // and goes out as twenty, so counting the template understates every one of
  // them — and does it worst right at the boundary where it changes the bill.
  const preview = fillSample(effective, spec.sample || {});
  const { length, parts, unicode, offenders } = smsParts(preview);
  const using = value.trim() ? "yours" : "ours";

  return (
    <div className="rounded-lg border border-white/10 bg-slate-950/40 p-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <label className="text-sm font-medium text-slate-300">{spec.label}</label>
        <span className="text-[11px] uppercase tracking-wider text-slate-500">{using}</span>
      </div>

      <textarea
        name={name}
        value={value}
        onChange={onChange}
        rows={4}
        spellCheck={false}
        placeholder={spec.default}
        className={`w-full rounded-lg border bg-slate-950 px-3 py-2 font-mono text-xs leading-relaxed text-slate-100 focus:outline-none focus:ring-2 ${
          error
            ? "border-red-500/60 focus:ring-red-500"
            : "border-white/15 focus:ring-blue-500"
        }`}
      />

      {/* What the customer gets. Worth showing rather than describing: the
          count below is taken from this text, and an operator can see at a
          glance that a line they meant to keep vanished with an empty value. */}
      <pre className="mt-2 whitespace-pre-wrap rounded border border-white/5 bg-black/30 px-3 py-2 text-[11px] leading-relaxed text-slate-400">
        {preview}
      </pre>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className={`text-xs ${parts > 1 ? "text-amber-300" : "text-slate-400"}`}>
          {length} characters · <b>{parts === 1 ? "1 SMS" : `${parts} SMS`}</b>
          {unicode && " · unicode"}
        </span>
        {spec.placeholders.map((p) => (
          <code
            key={p}
            title={spec.required.includes(p) ? "Required in this message" : undefined}
            className={`rounded px-1.5 py-0.5 text-[11px] ${
              spec.required.includes(p)
                ? "bg-emerald-500/10 text-emerald-300"
                : "bg-white/5 text-slate-400"
            }`}
          >
            {`{${p}}`}
          </code>
        ))}
      </div>

      {unicode && (
        <p className="mt-1.5 text-xs text-amber-300">
          {offenders.slice(0, 5).join(" ")} {offenders.length === 1 ? "is" : "are"}{" "}
          outside the standard SMS alphabet, so a part holds 70 characters
          instead of 160.
        </p>
      )}
      {error && <p className="mt-1.5 text-xs text-red-400">{error}</p>}
    </div>
  );
}

function Field({ label, name, value, onChange, placeholder = "" }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-300 mb-1.5">{label}</label>
      <input
        name={name}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="w-full border border-white/15 bg-slate-950 text-slate-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
      />
    </div>
  );
}

const testColors = {
  emerald: "bg-emerald-600 hover:bg-emerald-700",
  amber:   "bg-amber-500/100  hover:bg-amber-600",
  violet:  "bg-violet-600 hover:bg-violet-700",
};

function TestBtn({ label, color, loading, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className={`mt-1 ${testColors[color]} text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors`}
    >
      {loading ? "Testing…" : label}
    </button>
  );
}
