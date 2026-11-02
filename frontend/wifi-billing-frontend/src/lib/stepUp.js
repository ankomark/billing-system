/**
 * Asking for a two-step code in the middle of something.
 *
 * Some actions — changing M-Pesa credentials, adding staff — are refused by
 * the server with code "mfa_step_up_required" unless a code was confirmed in
 * the last ten minutes. The API client catches that answer and calls
 * requestStepUp(); <StepUpPrompt/> shows the dialog, confirms the code, and
 * the original request is retried as if nothing had happened.
 *
 * One prompt at a time: requests that hit the same wall while the dialog is
 * open all wait on the same answer.
 */

let listener = null;
let pending = null;

export function onStepUpRequest(fn) {
  listener = fn;
  return () => {
    if (listener === fn) listener = null;
  };
}

export function requestStepUp() {
  if (!listener) return Promise.reject(new Error("No step-up prompt mounted"));
  if (!pending) {
    pending = new Promise((resolve, reject) => listener({ resolve, reject })).finally(() => {
      pending = null;
    });
  }
  return pending;
}
