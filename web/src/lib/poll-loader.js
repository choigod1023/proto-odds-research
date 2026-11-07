/** One in-flight batch; only valid JSON replaces the last good value. */
export function createPollLoader(sources, publish, { fetcher = fetch, timeoutMs = 15000, fallbacks = {}, merge = {} } = {}) {
  const cache = new Map();
  const unsupported = new Set();
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
          const request = (target) => {
            const headers = { 'Cache-Control': 'no-cache' };
            if (previous?.url === target && previous.etag) headers['If-None-Match'] = previous.etag;
            return fetcher(`${target}${target.includes('?') ? '&' : '?'}_=${Date.now()}`, {
              cache: 'no-store', headers, signal: controller.signal,
            });
          };
          let target = unsupported.has(key) ? fallbacks[key] : url;
          let response = await request(target);
          if (target === url && fallbacks[key] && (response.status === 404 || (!previous && response.status === 503))) {
            if (response.status === 404) unsupported.add(key);
            target = fallbacks[key];
            response = await request(target);
          }
          if (response.status === 304 && previous) { data[key] = previous.value; return; }
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const text = await response.text();
          if (stopped || controller.signal.aborted) { data[key] = previous?.value ?? null; return; }
          const etag = response.headers?.get('ETag') || null;
          if (previous?.text === text) { cache.set(key, { ...previous, etag, url: target }); data[key] = previous.value; }
          else {
            const incoming = JSON.parse(text);
            const value = merge[key] ? merge[key](previous?.value, incoming) : incoming;
            cache.set(key, { text, value, etag, url: target });
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
