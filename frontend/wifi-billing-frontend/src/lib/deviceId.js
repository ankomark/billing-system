/**
 * A random id this browser keeps for itself, sent with every request.
 *
 * It is how the server tells "a new device" from "the same laptop on a
 * different network": IP addresses change all day on mobile data, so they
 * cannot. Not a secret and not used to authenticate anything — only to decide
 * whether a sign-in deserves a "was this you?" text.
 */
const KEY = "device_id";

export default function deviceId() {
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id =
        (window.crypto?.randomUUID && window.crypto.randomUUID()) ||
        Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}
