import { useMemo, useState } from 'react';
import { overallAccuracy, SPORT_NAMES, accuracyTime } from '../lib/overall-accuracy.js';
export default function OverallAccuracy({data}) {
  const stats=useMemo(()=>overallAccuracy(data),[data]);
  const [sport,setSport]=useState('all'),[outcome,setOutcome]=useState('all'),[page,setPage]=useState(0);
  const rows=(stats?.rows||[]).filter(r=>(sport==='all'||(r.sport||'unknown')===sport)&&(outcome==='all'||r.state===outcome));
  const current=Math.min(page,Math.max(0,Math.ceil(rows.length/20)-1));
  const labels={hit:'적중',miss:'실패',void:'무효',pending:'미판정'};
  return <section className="overall-accuracy" aria-label="전체 예측 적중률">
    <div><p className="accuracy-kicker">누적 예측 적중률</p><div className="accuracy-value">{stats?.rate != null ? `${(stats.rate*100).toFixed(1)}%` : '—'}<span>{stats?.settled ? `${stats.settled.toLocaleString()}경기 중 ${stats.hit.toLocaleString()}경기 적중` : '집계 대기'}</span></div></div>
    <p className="accuracy-note">전체 저장 사전 픽 기준이며 관심 픽만의 성적은 아닙니다. 적중률 = 적중 ÷ (적중 + 실패). 무효·미판정은 분모에서 제외합니다. 적중률은 수익률이 아닙니다.</p>
    <p className="accuracy-note">집계 데이터 생성: {accuracyTime(data?.generated_at)} (KST). 갱신 중에는 마지막 정상 집계를 유지합니다.</p>
    <p className="accuracy-note">전체 {stats?.total||0} · 무효 {stats?.void||0} · 미판정 {stats?.pending||0} · 점수 기반 임시 판정 {stats?.provisional||0} (적중·실패에 포함, 공식 결과로 변경될 수 있음)</p>
    <p className="accuracy-note">인기 경기 여부: 조회수·관심 등록 집계가 연결되지 않아 확인할 수 없습니다. 경기 시각·기록 시각은 모두 한국 시간(KST)입니다.</p>
    <div className="accuracy-sports" aria-label="종목별 적중률">
      {(stats?.sports||[]).map(g=><button key={g.sport} aria-pressed={sport===g.sport} onClick={()=>{setSport(sport===g.sport?'all':g.sport);setPage(0);}}>
        <strong>{SPORT_NAMES[g.sport]||g.sport}</strong><b>{g.rate==null?'집계 대기':`${(g.rate*100).toFixed(1)}%`}</b>
        <span>적중 {g.hit} / 실패 {g.miss}</span><span>무효 {g.void} · 미판정 {g.pending} · 임시 {g.provisional}</span>
      </button>)}
    </div>
    <details className="accuracy-history">
      <summary>어떤 경기를 맞혔나요? 경기별 기록 보기</summary>
      <div className="accuracy-filters">
        <label>종목 <select value={sport} onChange={e=>{setSport(e.target.value);setPage(0);}}><option value="all">전체 종목</option>{(stats?.sports||[]).map(g=><option key={g.sport} value={g.sport}>{SPORT_NAMES[g.sport]||g.sport}</option>)}</select></label>
        <label>결과 <select value={outcome} onChange={e=>{setOutcome(e.target.value);setPage(0);}}><option value="all">전체 결과</option>{Object.entries(labels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
        <span>{rows.length}경기 · 경기 시각 최신순</span>
      </div>
      {!rows.length&&<p>이 조건에 해당하는 기록이 없습니다.</p>}
      <ol className="accuracy-records">
        {rows.slice(current*20,(current+1)*20).map(r=><li key={r.id}>
          <div><span className={`accuracy-outcome ${r.state}`}>{labels[r.state]}{r.provisional?' · 임시':''}</span> {SPORT_NAMES[r.sport]||r.sport||'종목 정보 없음'} · {r.league||'리그 정보 없음'}</div>
          <strong>{r.home&&r.away?`${r.home} vs ${r.away}`:'경기명 정보 없음'}</strong>
          <p>경기 시작: {accuracyTime(r.kickoff)} (KST)</p>
          <p>당시 픽: {[r.market,r.label,r.selection].filter(Boolean).join(' · ')||'선택 정보 없음'} · 배당 {r.odds??'정보 없음'}</p>
          <p>예측 기록: {accuracyTime(r.captured_at)} · 판정 기록: {accuracyTime(r.settled_at)}</p>
          <small>예측 ID: {r.id}</small>
        </li>)}
      </ol>
      {rows.length>20&&<nav className="accuracy-filters" aria-label="경기 기록 페이지"><button disabled={current===0} onClick={()=>setPage(current-1)}>이전</button><span>{current+1} / {Math.ceil(rows.length/20)}</span><button disabled={(current+1)*20>=rows.length} onClick={()=>setPage(current+1)}>다음</button></nav>}
    </details>
  </section>;
}
