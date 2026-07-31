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
import { useEffect, useRef, useState, useCallback } from "react";
import { AlertTriangle, X } from "lucide-react";

export default function ConfirmModal({
  open,
  title,
  description,
  confirmText = "Confirm",
  cancelText = "Cancel",
  danger = false,
  onConfirm,
  onCancel,
}) {
  const cancelRef = useRef(null);

  useEffect(() => {
    if (open) cancelRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative bg-slate-900/80 rounded-2xl shadow-2xl max-w-md w-full p-6">
        <button
          onClick={onCancel}
          className="absolute top-4 right-4 text-slate-500 hover:text-slate-300 transition-colors"
        >
          <X size={18} />
        </button>
        <div className="flex gap-4">
          <div className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${
            danger ? "bg-red-100" : "bg-amber-100"
          }`}>
            <AlertTriangle size={20} className={danger ? "text-red-300" : "text-amber-300"} />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-bold text-white text-base leading-tight">{title}</h3>
            {description && (
              <p className="text-slate-400 text-sm mt-1.5 leading-relaxed">{description}</p>
            )}
            <div className="flex gap-3 mt-5">
              <button
                onClick={onConfirm}
                className={`flex-1 py-2 rounded-lg text-sm font-semibold text-white transition-colors ${
                  danger ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700"
                }`}
              >
                {confirmText}
              </button>
              <button
                ref={cancelRef}
                onClick={onCancel}
                className="flex-1 py-2 rounded-lg text-sm font-semibold border border-white/15 text-slate-300 hover:bg-white/5 transition-colors"
              >
                {cancelText}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function useConfirm() {
  const [state, setState] = useState({
    open: false,
    resolve: null,
    title: "",
    description: "",
    confirmText: "Confirm",
    danger: false,
  });

  const confirm = useCallback(
    (options) =>
      new Promise((resolve) => {
        setState({ open: true, resolve, confirmText: "Confirm", danger: false, ...options });
      }),
    []
  );

  const handleConfirm = useCallback(() => {
    setState((s) => { s.resolve(true); return { ...s, open: false }; });
  }, []);

  const handleCancel = useCallback(() => {
    setState((s) => { s.resolve(false); return { ...s, open: false }; });
  }, []);

  const ConfirmDialog = useCallback(
    () => (
      <ConfirmModal
        open={state.open}
        title={state.title}
        description={state.description}
        confirmText={state.confirmText}
        danger={state.danger}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
    ),
    [state, handleConfirm, handleCancel]
  );

  return { confirm, ConfirmDialog };
}
