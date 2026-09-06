import { overallAccuracy } from '../lib/overall-accuracy.js';
export default function OverallAccuracy({data}) {
  const stats=overallAccuracy(data);
  return <section className="overall-accuracy" aria-label="전체 예측 적중률">
    <div><p className="accuracy-kicker">누적 예측 적중률</p><div className="accuracy-value">{stats?.rate != null ? `${(stats.rate*100).toFixed(1)}%` : '—'}<span>{stats?.settled ? `${stats.settled.toLocaleString()}경기 중 ${stats.hit.toLocaleString()}경기 적중` : '집계 대기'}</span></div></div>
  </section>;
}
