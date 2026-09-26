import { useEffect, useState } from 'react';

const URL = 'https://proto-odds-collector.fly.dev/api/today-recommendations';
const number = value => Number.isFinite(value) ? value.toLocaleString('ko-KR') : '—';
const pct = value => Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '산정 전';
const date = value => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '확인 전';
const reasons = { unvalidated_probability: '검증된 확률·불확실성 근거 부족',
  insufficient_conservative_value: '보수적으로 계산한 배당 가치 부족',
  invalid_probability_or_price: '확률 또는 배당 정보 오류' };
const result = pick => ({ hit: '적중', miss: '실패', void: '무효' }[pick?.result] || '정산 대기');
const pickText = pick => pick
  ? `${pick.market || ''} ${pick.market_label || ''} · ${pick.sel || ''} · ${number(pick.odds)}배 · ${result(pick)}`
  : '선택 없음';

export function ShadowView({ payload, error, loading, retry, now = Date.now() }) {
  const [limit, setLimit] = useState(20);
  const state = payload?.per_event_shadow;
  const age = now - Date.parse(payload?.generated_at);
  const stale = state && (state.source_status !== 'fresh' || !Number.isFinite(age) || age > 15 * 60000 || age < 0);
  const records = Object.values(state?.records || {}).sort((a, b) =>
    String(b.recorded_at).localeCompare(String(a.recorded_at)) || String(a.id).localeCompare(String(b.id)));
  const status = { recording: '기록 중', closed_settling: '신규 기록 종료 · 결과 정산 중',
    awaiting_fresh_source: '최신 원천 대기' }[state?.status] || '실험 기록 확인 전';
  return <section id="per-event-shadow" className="recommendation-results per-event-shadow" aria-label="경기별 픽 가상 비교">
    <h2>경기별 픽 가상 비교</h2>
    <p>실험용 · 실제 베팅 및 정식 추천과 별개입니다. 기존 추천은 유지합니다.</p>
    <p role="status">{loading && !payload ? '실험 기록 불러오는 중…' : status}</p>
    {error && <p role="alert">실험 API를 불러오지 못했습니다. {payload ? '마지막으로 받은 기록을 유지합니다.' : '0건이나 추천 없음으로 판단하지 마세요.'}</p>}
    {stale && <p role="alert">오래되었거나 불완전한 원천입니다. 아래 기록을 최신 상태로 해석하지 마세요.</p>}
    <button type="button" onClick={retry} disabled={loading}>{loading ? '조회 중' : '실험 기록 새로고침'}</button>
    {!state && !loading && !error && <p>응답에 가상 비교 기록이 아직 없습니다. 기존 추천 결과와는 별도 집계입니다.</p>}
    {state?.policy && <>
      <p>기록 기간 {date(state.started_at)} ~ {date(state.ends_at)} KST</p>
      <p>산출물 생성 {date(payload.generated_at)} KST · 관측 {number(state.observed_events)}경기 · 새 방식 보류 {number(state.abstained_events)}경기</p>
      {state.capacity_reached && <p role="alert">기록 상한에 도달했습니다. 신규 경기 등록은 중단됩니다.</p>}
      <div className="dashboard-summary">
        {['baseline', 'challenger'].map(arm => {
          const s = state.summary?.[arm];
          return <div key={arm}><small>{arm === 'baseline' ? '기존 픽 · 동시점 기준' : '새 방식 · 가상 픽'}</small>
            <b>정산 ROI {s?.settled > 0 ? pct(s.roi) : '산정 전'}</b>
            <span>선택 {number(s?.selected)} · 정산 {number(s?.settled)} · 대기 {number(s?.pending)}</span>
            <span>적중 {number(s?.hits)} · 실패 {number(s?.misses)} · 무효 {number(s?.void)}</span>
            <span>정산 손익 {s?.settled > 0 ? `${number(s.profit_units)}단위` : '산정 전'}</span>
          </div>;
        })}
      </div>
      <p>두 방식 모두 정산된 동일 경기 {number(state.summary?.paired?.settled_events)}건 · 새 방식 − 기존 손익 차이 {state.summary?.paired?.settled_events > 0 ? `${number(state.summary.paired.profit_difference_units)}단위` : '산정 전'}</p>
      <p className="recommendation-result-rules">선택당 가상 원금 1단위, 무효는 정산 원금에 포함합니다. 두 방식의 선택 경기 집합은 다를 수 있습니다. 이 ROI는 조합 수익률이나 미래 기대수익률이 아닙니다.</p>
      <details><summary>경기별 기존 픽·새 픽·보류 사유 ({records.length}건)</summary>
        <p>경기 시작 30분 이전 최초 동시 관측을 고정합니다. 현재 운영의 최종 픽과 다를 수 있습니다.</p>
        {records.slice(0, limit).map(r => {
          const identity = r.baseline || r.challenger;
          const why = Object.entries(r.reason_counts || {}).filter(([key, count]) => key !== 'eligible' && count > 0);
          return <article key={r.id} className="dashboard-bet-card">
            <h3>{identity ? `${identity.home} vs ${identity.away}` : `경기 ${r.id}`}</h3>
            <p>{identity?.league} · 시작 {date(r.kickoff_at)} KST</p>
            <p>기존 픽: {pickText(r.baseline)}</p>
            <p>새 방식: {r.challenger ? pickText(r.challenger) : '보류'}</p>
            {!r.challenger && <p>{why.length ? why.map(([key, count]) => `${reasons[key] || '기타 사유'} ${count}선택지`).join(' · ') : '사유 정보 없음'}</p>}
            <small>기록 {date(r.recorded_at)} KST · 후보 {number(r.candidate_count)}선택지</small>
          </article>;
        })}
        {records.length > limit && <button type="button" onClick={() => setLimit(n => n + 20)}>20경기 더 보기</button>}
      </details>
    </>}
  </section>;
}

export default function PerEventShadow() {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let stopped = false, controller;
    async function load() {
      if (controller) return;
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 15000);
      setLoading(true);
      try {
        const response = await fetch(`${URL}?_=${Date.now()}`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('API failure');
        const value = await response.json();
        if (!value || typeof value !== 'object' || !value.generated_at) throw new Error('Invalid artifact');
        if (!stopped) { setPayload(value); setError(false); }
      } catch { if (!stopped) setError(true); }
      finally { clearTimeout(timeout); controller = null; if (!stopped) setLoading(false); }
    }
    const visible = () => { if (document.visibilityState === 'visible') load(); };
    load();
    const timer = setInterval(visible, 60000);
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('online', visible);
    window.addEventListener('focus', visible);
    return () => { stopped = true; controller?.abort(); clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
      window.removeEventListener('online', visible); window.removeEventListener('focus', visible); };
  }, [revision]);
  return <ShadowView payload={payload} error={error} loading={loading} retry={() => setRevision(n => n + 1)} />;
}
