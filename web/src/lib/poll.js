import { useEffect, useState } from 'react';
import { createPollLoader } from './poll-loader.js';
import { startForegroundPoll } from './foreground-poll.js';

/** Failed requests preserve good JSON; unchanged responses do not rerender. */
export function usePolledData(sources, everyMs = 300000) {
  const [state, setState] = useState({ data: {}, at: null });
  const sourceKey = JSON.stringify(sources);
  useEffect(() => {
    const loader = createPollLoader(JSON.parse(sourceKey), setState);
    const stopPolling = startForegroundPoll(loader.load, everyMs);
    return () => {
      stopPolling();
      loader.stop();
    };
  }, [sourceKey, everyMs]);
  return state;
}
