/** One request at a time, bounded through JSON body consumption, abortable on unmount. */
export function createJsonPoll(url, onData, onError, timeoutMs = 15000) {
  let controller = null;
  let stopped = false;
  return {
    async load() {
      if (controller || stopped) return;
      const current = new AbortController();
      controller = current;
      const timeout = setTimeout(() => current.abort(), timeoutMs);
      try {
        const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`, {
          signal: current.signal, cache: 'no-store',
          headers: { 'Cache-Control': 'no-cache' },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!stopped && !current.signal.aborted) onData(data);
      } catch (error) {
        if (!stopped) onError(error);
      } finally {
        clearTimeout(timeout);
        controller = null;
      }
    },
    stop() { stopped = true; controller?.abort(); },
  };
}
