export default function EmptyState({ icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      {icon && (
        <div className="w-14 h-14 bg-white/5 rounded-full flex items-center justify-center mb-4 text-slate-500">
          {icon}
        </div>
      )}
      <p className="font-semibold text-slate-300 text-base">{title}</p>
      {description && (
        <p className="text-slate-500 text-sm mt-1 max-w-xs">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
