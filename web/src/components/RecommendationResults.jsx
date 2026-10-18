import { useState } from 'react';
import { recommendationResults } from '../lib/recommendation-results.js';
import GameInfoModal from './GameInfoModal.jsx';
import { SPORT_NAMES, accuracyTime } from '../lib/overall-accuracy.js';

export default function RecommendationResults({today,data,odds,now=Date.now()}) {
  const result = recommendationResults(today,data,odds,now);
  const [opened,setOpened] = useState(null);
  const selected = result.settled.find(r=>r.id===opened);
  const date = r => accuracyTime(new Date(r.kickoff).toISOString());
  const sportName = r => SPORT_NAMES[r.sport] || SPORT_NAMES.unknown;
  const sports = [...new Set(result.settled.map(sportName))].map(name => {
    const rows = result.settled.filter(r => sportName(r) === name);
    return {name, total:rows.length, hit:rows.filter(r => r.outcome.state === 'hit').length};
  });
  return <section className="recommendation-results" aria-label="최근 추천 결과">
    <h2>{result.settled.length ? <>최근 추천 {result.settled.length}건 중 <strong>{result.hit}건 적중</strong></> : '추천 결과를 기다리고 있어요'}</h2>
    <p className="recommendation-result-scope">당시 추천 정책 기준 · 이전 정책 기록 포함. 현재 55%·배당 1.50 이상 정책만의 성적이 아닙니다.</p>
    <p className="recommendation-result-rules">최근 90일 사전 보존 기록 · 공식 결과 확정된 최근 최대 10건 · 진행 중·무효 제외</p>
    {result.settled.length ? <>
      <ul className="recommendation-result-sports" aria-label="최근 추천 내 종목별 적중">
        {sports.map(s => <li key={s.name}>{s.name} · {s.total}건 중 {s.hit}건 적중</li>)}
      </ul>
      <p className="recommendation-result-rules">아래 최근 {result.settled.length}건만 집계한 성적입니다. 전체 적중률이나 인기 경기 순위가 아닙니다.</p>
      <div className="recommendation-result-tiles">
        {result.settled.map(r=><button type="button" key={r.id} className={`recommendation-result-tile is-${r.outcome.state}`}
          aria-haspopup="dialog" onClick={()=>setOpened(r.id)}
          aria-label={`${sportName(r)} ${date(r)} KST ${r.home} 대 ${r.away} ${r.outcome.state==='hit'?'적중':'실패'} 경기정보`}>
          <span className="recommendation-result-card-heading"><small>{sportName(r)} · {r.league || '리그 정보 없음'}</small><b>{r.outcome.state==='hit'?'적중':'실패'}</b></span>
          <span className="recommendation-result-match">{r.home || '홈팀 정보 없음'} vs {r.away || '원정팀 정보 없음'}</span>
          <time dateTime={new Date(r.kickoff).toISOString()}>{date(r)} KST</time>
        </button>)}
      </div>
      <p className="recommendation-result-rules">최근 경기부터 표시 · 칸을 누르면 경기와 당시 추천을 확인할 수 있어요.</p>
    </> : <p>확인되지 않은 과거 추천은 넣지 않고, 새로 보존한 추천부터 결과를 쌓습니다.</p>}
    {(result.upcoming>0 || result.pending>0 || result.void>0) && <p className="recommendation-result-rules">경기 전 {result.upcoming}건 · 진행 중·결과 확인 중 {result.pending}건 · 무효 {result.void}건</p>}
    {selected && <GameInfoModal title={`${selected.home} vs ${selected.away}`} onClose={()=>setOpened(null)}>
      <p>{sportName(selected)} · {selected.league} · {date(selected)} KST</p>
      <h3>{selected.market} {selected.market_label} · {selected.sel}</h3>
      <p>당시 배당 {Number(selected.odds).toFixed(2)}배</p>
      <p>공식 결과: {selected.outcome.state==='hit'?'적중':'실패'}</p>
      <p>추천 기록 시각: {new Date(selected.recorded_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}</p>
      <small>경기 시작 30분 전 보존된 오늘의 추천픽 기준</small>
    </GameInfoModal>}
  </section>;
}
