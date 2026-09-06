import React, { useState } from "react";
const val = (n) => Number.isInteger(n) && n >= 0 ? n : "—";
const periodLabel = (sport, n) => sport === "sc" ? ({1:"전반",2:"후반",3:"연장 전반",4:"연장 후반",5:"승부차기"}[n] || `${n}구간`) : sport === "bk" ? n <= 4 ? `${n}쿼터` : `연장 ${n-4}` : sport === "vl" ? `${n}세트` : `${n}구간`;
const symbols = { GOAL:"●", YELLOW_CARD:"🟨", RED_CARD:"🟥", YELLOW_RED_CARD:"🟥", SUBSTITUTE:"⇄", FULLTIME:"■", HALFTIME:"Ⅱ" };
export default function MatchProgress({ game, live, phase, score }) {
  const [expanded, setExpanded] = useState(false);
  const periods = Array.isArray(live?.period_scores) ? live.period_scores : [];
  const events = Array.isArray(live?.timeline) ? live.timeline : [];
  if (game.sport === "bs") {
    const count = Math.max(9, ...periods.map(p => p.period), live?.inning || 0);
    const innings = Array.from({length:Math.min(count,50)}, (_,i)=>i+1);
    return <section className="match-progress" aria-label="이닝별 스코어보드">
      <h3>이닝별 스코어보드</h3>
      <div className="innings-scroll" tabIndex="0" role="region" aria-label="이닝별 점수 · 가로 스크롤">
        <table className="innings-table"><thead><tr><th scope="col">팀</th>{innings.map(n=><th scope="col" key={n} className={n===live?.inning?"current-inning":""}>{n}</th>)}<th scope="col">R</th></tr></thead>
          <tbody>{["away","home"].map(side=><tr key={side}><th scope="row">{game[side]}<small>{side==="away"?"원정":"홈"}</small></th>{innings.map(n=><td key={n} className={n===live?.inning?"current-inning":""}>{val(periods.find(p=>p.period===n)?.[side])}</td>)}<td className="innings-total">{val(score?.[side==="home"?0:1] ?? live?.[`${side}_score`])}</td></tr>)}</tbody>
        </table>
      </div><p className="progress-note">{periods.length ? "— 미제공 또는 아직 진행하지 않은 이닝 · R 총 득점" : phase === "upcoming" ? "경기가 시작되면 이닝별 점수를 표시합니다." : "이닝별 기록을 기다리고 있습니다. · R 총 득점"}</p>
    </section>;
  }
  const visible = expanded ? events : events.slice(-6);
  return <section className="match-progress" aria-label="경기 타임라인">
    <div className="progress-heading"><h3>경기 타임라인</h3><span>{live?.status_text || (phase === "upcoming" ? "경기 전" : "기록 확인 중")}</span></div>
    {periods.length > 0 && <ol className="period-flow" aria-label="구간별 점수 흐름">{periods.map(p=><li key={p.period} className={p.period===live?.current_period && phase!=="finished"?"is-current":""}><b>{periodLabel(game.sport,p.period)}</b><strong>{val(p.home)} : {val(p.away)}</strong></li>)}</ol>}
    {periods.length > 0 && <p className="progress-note">{game.home} : {game.away} · 각 구간 득점</p>}
    {events.length > 0 ? <>
      <p className="progress-note">{live?.timeline_scope === "latest" ? "최근 제공 이벤트" : "제공된 주요 이벤트 · 시간순"}</p>
      {!expanded && events.length>6 && <button className="timeline-expand" onClick={()=>setExpanded(true)}>이전 이벤트 {events.length-6}개 펼치기</button>}
      <ol className="event-timeline">{visible.map((event,i)=><li key={`${event.period}-${event.time}-${i}`} className={`event-${event.side}`}>
        <div className="event-time">{({HALFTIME:"HT",FULLTIME:"FT"}[event.type]) || event.time || (event.period ? periodLabel(game.sport,event.period) : "기록")}<i aria-hidden="true">{symbols[event.type] || "•"}</i></div>
        <div><small>{event.side==="home"?game.home:event.side==="away"?game.away:"경기 진행"}</small><p>{event.text}</p></div>
      </li>)}</ol>
      {expanded && events.length>6 && <button className="timeline-expand" onClick={()=>setExpanded(false)}>최근 이벤트만 보기</button>}
    </> : <p className="progress-empty">{phase === "upcoming" ? "경기가 시작되면 진행 기록이 표시됩니다." : "상세 이벤트 기록이 아직 제공되지 않았습니다."}</p>}
  </section>;
}
