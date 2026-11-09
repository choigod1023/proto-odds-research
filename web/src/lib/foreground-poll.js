/** Share the same visibility rule for scores, odds and match requests. */
export function startForegroundPoll(load, everyMs, { page = document, target = window } = {}) {
  const refresh = () => {
    if (page.visibilityState === "visible") load();
  };
  refresh();
  const timer = everyMs > 0 ? target.setInterval(refresh, everyMs) : null;
  page.addEventListener("visibilitychange", refresh);
  target.addEventListener("focus", refresh);
  target.addEventListener("online", refresh);
  return () => {
    if (timer !== null) target.clearInterval(timer);
    page.removeEventListener("visibilitychange", refresh);
    target.removeEventListener("focus", refresh);
    target.removeEventListener("online", refresh);
  };
}
