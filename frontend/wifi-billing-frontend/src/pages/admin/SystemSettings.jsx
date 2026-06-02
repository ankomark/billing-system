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
  testWhatsapp,
} from "../../services/settings";

const EMPTY = {
  MPESA_CONSUMER_KEY: "",
  MPESA_CONSUMER_SECRET: "",
  MPESA_SHORTCODE: "",
  MPESA_PASSKEY: "",
  MPESA_CALLBACK_URL: "",
  AT_USERNAME: "",
  AT_API_KEY: "",
  WHATSAPP_TOKEN: "",
  WHATSAPP_PHONE_ID: "",
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
      toast.success(res?.message || "Connection successful");
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
          <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
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
          <h1 className="text-2xl font-bold text-slate-800">System Settings</h1>
          <p className="text-slate-500 text-sm mt-1">
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

          {/* Africa's Talking */}
          <Section title="Africa's Talking (SMS)">
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="AT Username" name="AT_USERNAME" value={form.AT_USERNAME} onChange={handleChange} />
              <Field label="AT API Key"  name="AT_API_KEY"  value={form.AT_API_KEY}  onChange={handleChange} />
            </div>
            <TestBtn label="Test SMS" color="amber" loading={testing === "sms"} onClick={() => runTest("sms")} />
          </Section>

          {/* WhatsApp */}
          <Section title="WhatsApp Cloud API">
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="WhatsApp Token"   name="WHATSAPP_TOKEN"    value={form.WHATSAPP_TOKEN}    onChange={handleChange} />
              <Field label="Phone Number ID"  name="WHATSAPP_PHONE_ID" value={form.WHATSAPP_PHONE_ID} onChange={handleChange} />
            </div>
            <TestBtn label="Test WhatsApp" color="violet" loading={testing === "whatsapp"} onClick={() => runTest("whatsapp")} />
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
    <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{title}</p>
      {children}
    </div>
  );
}

function Field({ label, name, value, onChange, placeholder = "" }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1.5">{label}</label>
      <input
        name={name}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
      />
    </div>
  );
}

const testColors = {
  emerald: "bg-emerald-600 hover:bg-emerald-700",
  amber:   "bg-amber-500  hover:bg-amber-600",
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
