import { useState } from 'react';
import { recommendationResults } from '../lib/recommendation-results.js';
import GameInfoModal from './GameInfoModal.jsx';

export default function RecommendationResults({today,data,odds,now=Date.now()}) {
  const result = recommendationResults(today,data,odds,now);
  const [opened,setOpened] = useState(null);
  const selected = result.settled.find(r=>r.id===opened);
  const date = r => new Date(r.kickoff).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric'});
  return <section className="recommendation-results" aria-label="최근 추천 결과">
    <h2>{result.settled.length ? <>최근 추천 {result.settled.length}건 중 <strong>{result.hit}건 적중</strong></> : '추천 결과를 기다리고 있어요'}</h2>
    <p className="recommendation-result-scope">오늘의 추천픽 기준</p>
    <p className="recommendation-result-rules">최근 90일 사전 보존 기록 · 공식 결과 확정된 최근 최대 10건 · 진행 중·무효 제외</p>
    {result.settled.length ? <>
      <div className="recommendation-result-tiles">
        {result.settled.map(r=><button type="button" key={r.id} className={`recommendation-result-tile is-${r.outcome.state}`}
          aria-haspopup="dialog" onClick={()=>setOpened(r.id)}
          aria-label={`${date(r)} ${r.home} 대 ${r.away} ${r.outcome.state==='hit'?'적중':'실패'} 경기정보`}>
          <b>{r.outcome.state==='hit'?'적중':'실패'}</b><small>{date(r)}</small>
        </button>)}
      </div>
      <p className="recommendation-result-rules">최근 경기부터 표시 · 칸을 누르면 경기와 당시 추천을 확인할 수 있어요.</p>
    </> : <p>확인되지 않은 과거 추천은 넣지 않고, 새로 보존한 추천부터 결과를 쌓습니다.</p>}
    {(result.upcoming>0 || result.pending>0 || result.void>0) && <p className="recommendation-result-rules">경기 전 {result.upcoming}건 · 진행 중·결과 확인 중 {result.pending}건 · 무효 {result.void}건</p>}
    {selected && <GameInfoModal title={`${selected.home} vs ${selected.away}`} onClose={()=>setOpened(null)}>
      <p>{selected.league} · {selected.date}</p>
      <h3>{selected.market} {selected.market_label} · {selected.sel}</h3>
      <p>당시 배당 {Number(selected.odds).toFixed(2)}배</p>
      <p>공식 결과: {selected.outcome.state==='hit'?'적중':'실패'}</p>
      <p>추천 기록 시각: {new Date(selected.recorded_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}</p>
      <small>경기 시작 30분 전 보존된 오늘의 추천픽 기준</small>
    </GameInfoModal>}
  </section>;
}
