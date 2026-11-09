import {useEffect, useState} from 'react';
import {readMatchJson} from './match-data.js';
import {MATCH_LIST_REFRESH_MS} from './match-refresh-notice.js';
import {startForegroundPoll} from './foreground-poll.js';

const empty = {path:null,data:null,checked:false,error:false};

export function useMatchData(path, everyMs=MATCH_LIST_REFRESH_MS, read=readMatchJson) {
  const [state,setState]=useState(empty);
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    if (!path) {
      setState(empty);
      return;
    }
    let stopped=false, active=null;
    const load=async()=>{
      if (stopped || active) return;
      const controller=new AbortController(); active=controller;
      const timer=setTimeout(()=>controller.abort(),15000);
      try {
        const data=await read(path,controller.signal);
        if (!stopped) setState({path,read,data,checked:true,error:false});
      } catch (error) {
        if (!stopped) setState(old=>({path,read,data:(old.path===path && old.read===read) || (path.startsWith('/api/matches?') && old.path?.startsWith('/api/matches?'))?old.data:null,checked:true,error:true,errorStatus:error.status ?? null}));
      } finally {clearTimeout(timer); active=null;}
    };
    const stopPolling=startForegroundPoll(load,everyMs);
    return ()=>{stopped=true;stopPolling();active?.abort();};
  },[path,everyMs,attempt,read]);
  const previousList = path?.startsWith('/api/matches?') && state.path?.startsWith('/api/matches?');
  return {...(state.path===path && state.read===read?state:{data:previousList?state.data:null,checked:false,error:false}),retry:()=>setAttempt(n=>n+1)};
}
