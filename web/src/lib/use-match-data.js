import {useEffect, useState} from 'react';
import {readMatchJson} from './match-data.js';

export function useMatchData(path, everyMs=60000) {
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
      } catch {
        if (!stopped) setState(old=>({path,data:old.path===path || (path.startsWith('/api/matches?') && old.path?.startsWith('/api/matches?'))?old.data:null,checked:true,error:true}));
      } finally {clearTimeout(timer); active=null;}
    };
    load();
    const timer=everyMs ? setInterval(load,everyMs) : null;
    const visible=()=>{if(document.visibilityState==='visible') load();};
    document.addEventListener('visibilitychange',visible);
    window.addEventListener('focus',load); window.addEventListener('online',load);
    return ()=>{stopped=true;active?.abort();clearInterval(timer);
      document.removeEventListener('visibilitychange',visible);
      window.removeEventListener('focus',load);window.removeEventListener('online',load);};
  },[path,everyMs,attempt]);
  const previousList = path?.startsWith('/api/matches?') && state.path?.startsWith('/api/matches?');
  return {...(state.path===path?state:{data:previousList?state.data:null,checked:false,error:false}),retry:()=>setAttempt(n=>n+1)};
}
