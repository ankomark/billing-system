import { useEffect, useState } from "react";
import AdminLayout from "../../components/admin/AdminLayout";
import {
  fetchSystemSettings,
  updateSystemSettings,
  testMpesa,
  testSms,
  testWhatsapp,
} from "../../services/settings";

export default function SystemSettings() {
  const [form, setForm] = useState({
    MPESA_CONSUMER_KEY: "",
    MPESA_CONSUMER_SECRET: "",
    MPESA_SHORTCODE: "",
    MPESA_PASSKEY: "",
    MPESA_CALLBACK_URL: "",
    AT_USERNAME: "",
    AT_API_KEY: "",
    WHATSAPP_TOKEN: "",
    WHATSAPP_PHONE_ID: "",
  });

  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [testing, setTesting] = useState("");

  useEffect(() => {
    fetchSystemSettings().then((data) => setForm(data));
  }, []);

  const handleChange = (e) => {
    setForm({
      ...form,
      [e.target.name]: e.target.value,
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMessage("");

    try {
      await updateSystemSettings(form);
      setMessage("Settings saved successfully");
    } catch (err) {
      console.error(err);
      setMessage("❌ Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  // ==========================
  // Test Buttons Handlers
  // ==========================
  const runTest = async (type) => {
    setTesting(type);
    setMessage("");

    try {
      let res = null;

      if (type === "mpesa") res = await testMpesa();
      if (type === "sms") res = await testSms();
      if (type === "whatsapp") res = await testWhatsapp();

      setMessage("✅ " + (res.message || "Connection successful"));
    } catch (err) {
      setMessage("❌ Test failed: " + (err.response?.data?.error || err.message));
    } finally {
      setTesting("");
    }
  };

  return (
    <AdminLayout>
      <div className="p-6 max-w-3xl mx-auto">
        <h1 className="text-2xl font-bold mb-4">System Settings</h1>

        {message && (
          <div className="mb-4 text-sm p-2 rounded bg-blue-100 text-blue-800">
            {message}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-8 bg-white p-6 rounded shadow">

          {/* -------------------------------------------------- */}
          {/* M-PESA SETTINGS */}
          {/* -------------------------------------------------- */}

          <section>
            <h2 className="text-lg font-semibold mb-3">M-Pesa STK Push Settings</h2>

            <div className="grid md:grid-cols-2 gap-4">
              <Field
                name="MPESA_CONSUMER_KEY"
                value={form.MPESA_CONSUMER_KEY}
                placeholder="Consumer Key"
                onChange={handleChange}
              />

              <Field
                name="MPESA_CONSUMER_SECRET"
                value={form.MPESA_CONSUMER_SECRET}
                placeholder="Consumer Secret"
                onChange={handleChange}
              />

              <Field
                name="MPESA_SHORTCODE"
                value={form.MPESA_SHORTCODE}
                placeholder="Shortcode (174379)"
                onChange={handleChange}
              />

              <Field
                name="MPESA_PASSKEY"
                value={form.MPESA_PASSKEY}
                placeholder="Passkey"
                onChange={handleChange}
              />

              <Field
                name="MPESA_CALLBACK_URL"
                value={form.MPESA_CALLBACK_URL}
                placeholder="Callback URL"
                className="md:col-span-2"
                onChange={handleChange}
              />
            </div>

            <button
              type="button"
              onClick={() => runTest("mpesa")}
              className="mt-3 bg-green-600 text-white px-4 py-2 rounded disabled:opacity-50"
              disabled={testing === "mpesa"}
            >
              {testing === "mpesa" ? "Testing…" : "Test M-Pesa"}
            </button>
          </section>

          {/* -------------------------------------------------- */}
          {/* AFRICA'S TALKING SETTINGS */}
          {/* -------------------------------------------------- */}

          <section>
            <h2 className="text-lg font-semibold mb-3">Africa's Talking (SMS)</h2>

            <div className="grid md:grid-cols-2 gap-4">
              <Field
                name="AT_USERNAME"
                value={form.AT_USERNAME}
                placeholder="AT Username"
                onChange={handleChange}
              />

              <Field
                name="AT_API_KEY"
                value={form.AT_API_KEY}
                placeholder="AT API Key"
                onChange={handleChange}
              />
            </div>

            <button
              type="button"
              onClick={() => runTest("sms")}
              className="mt-3 bg-yellow-600 text-white px-4 py-2 rounded disabled:opacity-50"
              disabled={testing === "sms"}
            >
              {testing === "sms" ? "Testing…" : "Test SMS"}
            </button>
          </section>

          {/* -------------------------------------------------- */}
          {/* WHATSAPP CLOUD API SETTINGS */}
          {/* -------------------------------------------------- */}

          <section>
            <h2 className="text-lg font-semibold mb-3">WhatsApp Cloud API</h2>

            <div className="grid md:grid-cols-2 gap-4">
              <Field
                name="WHATSAPP_TOKEN"
                value={form.WHATSAPP_TOKEN}
                placeholder="WhatsApp Token"
                onChange={handleChange}
              />

              <Field
                name="WHATSAPP_PHONE_ID"
                value={form.WHATSAPP_PHONE_ID}
                placeholder="Phone Number ID"
                onChange={handleChange}
              />
            </div>

            <button
              type="button"
              onClick={() => runTest("whatsapp")}
              className="mt-3 bg-purple-600 text-white px-4 py-2 rounded disabled:opacity-50"
              disabled={testing === "whatsapp"}
            >
              {testing === "whatsapp" ? "Testing…" : "Test WhatsApp"}
            </button>
          </section>

          {/* SAVE BUTTON */}

          <button
            type="submit"
            disabled={saving}
            className="mt-4 bg-blue-600 text-white px-4 py-2 rounded font-semibold disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save Settings"}
          </button>

        </form>
      </div>
    </AdminLayout>
  );
}

/* --------------------------------------------
   Reusable component for input fields
---------------------------------------------*/
function Field({ name, value, onChange, placeholder, className = "" }) {
  return (
    <div className={className}>
      <label className="block text-sm mb-1">{name}</label>
      <input
        name={name}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="w-full border rounded px-3 py-2"
      />
    </div>
  );
}
