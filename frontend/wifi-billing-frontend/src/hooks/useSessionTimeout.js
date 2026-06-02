import { useEffect, useState, useCallback } from "react";

function decodeJwtExpiry(token) {
  try {
    const payload = token.split(".")[1];
    const decoded = JSON.parse(atob(payload));
    return decoded.exp ? decoded.exp * 1000 : null;
  } catch {
    return null;
  }
}

export default function useSessionTimeout({ warningMinutes = 5 } = {}) {
  const [secondsLeft, setSecondsLeft] = useState(null);
  const [showWarning, setShowWarning] = useState(false);

  const refresh = useCallback(() => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      setSecondsLeft(null);
      setShowWarning(false);
      return;
    }
    const expiry = decodeJwtExpiry(token);
    if (!expiry) return;
    const secs = Math.floor((expiry - Date.now()) / 1000);
    setSecondsLeft(secs > 0 ? secs : 0);
    setShowWarning(secs > 0 && secs <= warningMinutes * 60);
  }, [warningMinutes]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30_000);
    return () => clearInterval(interval);
  }, [refresh]);

  // Re-read after any storage change (token refresh in interceptor)
  useEffect(() => {
    const handler = () => refresh();
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, [refresh]);

  const dismiss = useCallback(() => setShowWarning(false), []);

  const minutesLeft = secondsLeft !== null ? Math.ceil(secondsLeft / 60) : null;

  return { showWarning, minutesLeft, dismiss };
}
