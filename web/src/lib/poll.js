import { useEffect, useState } from 'react';
import { createPollLoader } from './poll-loader.js';

/** Failed requests preserve good JSON; unchanged responses do not rerender. */
export function usePolledData(sources, everyMs = 300000) {
  const [state, setState] = useState({ data: {}, at: null });
  const sourceKey = JSON.stringify(sources);
  useEffect(() => {
    const loader = createPollLoader(JSON.parse(sourceKey), setState);
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
  }, [sourceKey, everyMs]);
  return state;
}
