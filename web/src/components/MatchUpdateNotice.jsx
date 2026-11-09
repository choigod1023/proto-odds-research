import { odds } from '../lib/fmt.js';
import { matchRefreshNotice } from '../lib/match-refresh-notice.js';

const koreanTime = value => new Date(value).toLocaleString('ko-KR', {
  timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});

export default function MatchUpdateNotice({ game, phase, now }) {
  const notice = matchRefreshNotice(game, { phase, ...(now == null ? {} : { now }) });
  if (!notice) return null;
  const previous = notice.previous;
  return <aside className="mx-3 my-2 rounded border border-rule bg-panel px-3 py-2 text-[12px] leading-6 text-ink2"
    role="status" aria-live="polite" aria-label="갱신 및 정산 안내">
    <strong className="text-ink">{notice.title}</strong>
    <p className="my-1">{notice.body}</p>
    {previous && <div className="my-1 border-l border-rule pl-2" aria-label="이전 추천 기록">
      <p className="my-0">
        <b className="text-ink">이전 추천</b> · {previous.option.market}{previous.option.label ? ` ${previous.option.label}` : ''}
        {' · '}{previous.option.선택} · 당시 배당 <span className="tnum">{odds(previous.option.배당)}배</span>
      </p>
      <p className="my-0 tnum text-ink3">이전 판정 {koreanTime(previous.capturedAt)} (한국 시간)</p>
    </div>}
    {notice.observedAt && <p className="my-0 tnum">새 배당 확인 {koreanTime(notice.observedAt)} (한국 시간)</p>}
    {notice.freezeAt && <p className="my-0 tnum">사전 픽 동결 {koreanTime(notice.freezeAt)} (한국 시간)</p>}
    {notice.followUp && <p className="my-1 text-ink3">{notice.followUp}</p>}
  </aside>;
}
