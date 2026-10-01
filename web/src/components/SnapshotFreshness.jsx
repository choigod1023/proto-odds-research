import { useEffect, useState } from 'react';

function generatedLabel(value, now) {
  const time = Date.parse(value || '');
  if (!Number.isFinite(time)) return '생성 시각 미확인';
  if (time > now + 60000) return '생성 시각 확인 필요';
  const minutes = Math.max(0, Math.floor((now - time) / 60000));
  return `${new Date(time).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST · ${minutes}분 전 생성`;
}

export function SnapshotFreshnessView({ sources, now }) {
  return <aside className="mb-4 rounded-lg border border-slate-200 px-4 py-3 text-sm" aria-label="데이터 갱신 상태">
    <b>데이터 갱신 상태</b>
    <p>조회 성공은 새 분석 완료를 뜻하지 않습니다. 각 자료의 생성 시각을 따로 확인하세요.</p>
    <ul>{sources.map(source => <li key={source.name}>
      <span>{source.name}: {source.error ? `최근 조회 실패${source.generatedAt ? ' · 마지막 정상값 유지' : ''}` : source.checked ? '최근 조회 성공' : '조회 중'}</span>
      {' / '}<span>{generatedLabel(source.generatedAt, now)}</span>
    </li>)}</ul>
  </aside>;
}

export default function SnapshotFreshness(props) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);
  return <SnapshotFreshnessView {...props} now={now} />;
}
