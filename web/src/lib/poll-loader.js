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
      await Promise.all(Object.entries(sources).map(async ([key, source]) => {
        const [primary, fallback] = Array.isArray(source) ? source : [source];
        const previous = cache.get(key);
        try {
          const request = (url) => {
            const headers = { 'Cache-Control': 'no-cache' };
            if (previous?.url === url && previous.etag) headers['If-None-Match'] = previous.etag;
            return fetcher(`${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`, {
              cache: 'no-store', headers, signal: controller.signal,
            });
          };
          let url = primary;
          const canFallback = fallback && previous?.url !== primary;
          const responseOrMissing = await request(url).catch((error) => {
            // An older edge may omit CORS headers for an unknown route.
            if (!canFallback || controller.signal.aborted || !(error instanceof TypeError)) throw error;
            return null;
          });
          let response = responseOrMissing;
          // Preserve a good compact response through temporary outages.
          if (canFallback && (!response || [404, 503].includes(response.status))) {
            url = fallback;
            response = await request(url);
          }
          if (response.status === 304 && previous?.url === url) { data[key] = previous.value; return; }
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const text = await response.text();
          if (stopped || controller.signal.aborted) { data[key] = previous?.value ?? null; return; }
          const etag = response.headers?.get('ETag') || null;
          if (previous?.text === text) { cache.set(key, { ...previous, etag, url }); data[key] = previous.value; }
          else {
            const value = JSON.parse(text);
            cache.set(key, { text, value, etag, url });
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
