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
      <div className="relative bg-white rounded-2xl shadow-2xl max-w-md w-full p-6">
        <button
          onClick={onCancel}
          className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 transition-colors"
        >
          <X size={18} />
        </button>
        <div className="flex gap-4">
          <div className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${
            danger ? "bg-red-100" : "bg-amber-100"
          }`}>
            <AlertTriangle size={20} className={danger ? "text-red-600" : "text-amber-600"} />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-bold text-slate-800 text-base leading-tight">{title}</h3>
            {description && (
              <p className="text-slate-500 text-sm mt-1.5 leading-relaxed">{description}</p>
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
                className="flex-1 py-2 rounded-lg text-sm font-semibold border border-slate-300 text-slate-700 hover:bg-slate-50 transition-colors"
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
