import { overallAccuracy } from '../lib/overall-accuracy.js';
export default function OverallAccuracy({data,checkedAt}) {
  const stats=overallAccuracy(data);
  return <section className="overall-accuracy" aria-label="전체 예측 적중률">
    <div><p className="accuracy-kicker">누적 예측 적중률</p><div className="accuracy-value">{stats?.rate != null ? `${(stats.rate*100).toFixed(1)}%` : '—'}<span>{stats?.settled ? `${stats.hit.toLocaleString()} / ${stats.settled.toLocaleString()}건 적중` : '판정된 예측 집계 대기'}</span></div></div>
    <div className="accuracy-context"><b>보존된 사전 예측 전체 기준</b>
      {stats ? <p>전체 {stats.total}건 · 미판정 {stats.pending}건 · 무효 {stats.void}건 제외</p> : <p>전체 예측 원장 집계를 기다리고 있습니다.</p>}
      <p>{stats?.scope === "saved_predictions" ? "기존 저장 기록 우선 집계 · 원장 연동 후 이어서 반영" : "예측 원장과 기존 저장 기록 통합 집계"}</p>
      {stats?.from && <p>집계 시작 {new Date(stats.from).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul'})}</p>}
      <p>종료 결과를 15초마다 확인 · {stats?.provisional || 0}건은 종료 점수 기준 임시 판정</p>
      {checkedAt && <small>경기 데이터 {new Date(checkedAt).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}</small>}
    </div>
  </section>;
}
