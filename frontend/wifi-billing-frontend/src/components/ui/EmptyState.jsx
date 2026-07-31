/**
 * DARK CONSOLE ONLY.
 *
 * This folder is named as though it were theme-neutral and is not: these were
 * darkened along with the operator console, and every page that imports them —
 * operator and platform alike — is dark.
 *
 * A light page using one of these will render invisibly, which is not a
 * hypothetical: the app-level loading screen did exactly that until it was
 * given its own neutral bars. If you need one of these on a light surface,
 * give it a variant rather than assuming it adapts.
 */
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
