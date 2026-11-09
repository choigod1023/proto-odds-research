import {useEffect, useState} from 'react';
import {readMatchJson} from './match-data.js';
import {MATCH_LIST_REFRESH_MS} from './match-refresh-notice.js';
import {startForegroundPoll} from './foreground-poll.js';

export function useMatchData(path, everyMs=MATCH_LIST_REFRESH_MS) {
  const [state,setState]=useState({path:null,data:null,checked:false,error:false});
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    if (!path) return;
    let stopped=false, active=null;
    const load=async()=>{
      if (stopped || active) return;
      const controller=new AbortController(); active=controller;
      const timer=setTimeout(()=>controller.abort(),15000);
      try {
        const data=await readMatchJson(path,controller.signal);
        if (!stopped) setState({path,data,checked:true,error:false});
      } catch (error) {
        if (!stopped) setState(old=>({path,data:old.path===path || (path.startsWith('/api/matches?') && old.path?.startsWith('/api/matches?'))?old.data:null,checked:true,error:true,errorStatus:error.status ?? null}));
      } finally {clearTimeout(timer); active=null;}
    };
    const stopPolling=startForegroundPoll(load,everyMs);
    return ()=>{stopped=true;stopPolling();active?.abort();};
  },[path,everyMs,attempt]);
  const previousList = path?.startsWith('/api/matches?') && state.path?.startsWith('/api/matches?');
  return {...(state.path===path?state:{data:previousList?state.data:null,checked:false,error:false}),retry:()=>setAttempt(n=>n+1)};
}
