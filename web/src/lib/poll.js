import { useEffect, useState } from 'react';
import { createPollLoader } from './poll-loader.js';

/** Failed requests preserve good JSON; unchanged responses do not rerender. */
export function usePolledData(sources, everyMs = 300000, { fallbacks, merge } = {}) {
  const [state, setState] = useState({ data: {}, at: null });
  const sourceKey = JSON.stringify(sources);
  const fallbackKey = JSON.stringify(fallbacks ?? {});
  useEffect(() => {
    const entries = JSON.parse(sourceKey);
    if (!Object.keys(entries).length) return;
    const loader = createPollLoader(entries, (result) => setState({...result, sourceKey}),
                                    { fallbacks: JSON.parse(fallbackKey), merge });
    const visible = () => { if (document.visibilityState === 'visible') loader.load(); };
    loader.load();
    const timer = setInterval(visible, everyMs);
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('online', visible);
    window.addEventListener('focus', visible);
    return () => {
      loader.stop(); clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
      window.removeEventListener('online', visible);
      window.removeEventListener('focus', visible);
    };
  }, [sourceKey, everyMs, fallbackKey, merge]);
  return state.sourceKey === sourceKey ? state : { data: {}, at: null };
}
