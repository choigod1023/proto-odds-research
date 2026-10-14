/** One in-flight batch; only valid JSON replaces the last good value. */
export function createPollLoader(sources, publish, { fetcher = fetch, timeoutMs = 15000 } = {}) {
  const cache = new Map();
  let active = null, stopped = false, initialized = false;
  async function load() {
    if (stopped || active) return;
    const controller = new AbortController();
    active = controller;
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const data = {};
    let changed = false;
    try {
      await Promise.all(Object.entries(sources).map(async ([key, url]) => {
        const previous = cache.get(key);
        try {
          const response = await fetcher(`${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`, {
            cache: 'no-store', headers: { 'Cache-Control': 'no-cache' }, signal: controller.signal,
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const text = await response.text();
          if (stopped || controller.signal.aborted) { data[key] = previous?.value ?? null; return; }
          if (previous?.text === text) data[key] = previous.value;
          else {
            const value = JSON.parse(text);
            cache.set(key, { text, value });
            data[key] = value;
            changed = true;
          }
        } catch { data[key] = previous?.value ?? null; }
      }));
      if (!stopped && (changed || !initialized)) {
        initialized = true;
        publish({ data, at: Date.now() });
      }
    } finally { clearTimeout(timer); active = null; }
  }
  return { load, stop() { stopped = true; active?.abort(); } };
}
