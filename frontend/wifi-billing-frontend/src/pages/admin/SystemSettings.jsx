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
};

export default function SystemSettings() {
  const qc = useQueryClient();
  const [form, setForm]     = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState("");

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
    try {
      await updateSystemSettings(form);
      toast.success("Settings saved successfully");
      qc.invalidateQueries({ queryKey: ["system-settings"] });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to save settings");
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
