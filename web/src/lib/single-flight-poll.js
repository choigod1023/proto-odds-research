// One outstanding request per source, including focus/online/manual refreshes.
export function singleFlightPoll({ read, success, failure, timeoutMs = 15000 }) {
  let stopped = false;
  let active = null;
  return {
    async load() {
      if (stopped || active) return;
      const controller = new AbortController();
      active = controller;
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const data = await read(controller.signal);
        if (!stopped) success(data);
      } catch (error) {
        if (!stopped) failure(error);
      } finally {
        clearTimeout(timer);
        active = null;
      }
    },
    stop() { stopped = true; active?.abort(); },
  };
}
