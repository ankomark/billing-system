// components/admin/UsageAlerts.jsx
export default function UsageAlerts({ alerts }) {
  if (!alerts.length) return null;

  return (
    <div className="bg-yellow-50 p-4 rounded shadow">
      <h3 className="font-semibold text-yellow-800 mb-2">
        Usage Alerts
      </h3>
      {alerts.map((a, i) => (
        <p key={i} className="text-sm">
          ⚠ {a.customer} — {a.percent}% used
        </p>
      ))}
    </div>
  );
}
