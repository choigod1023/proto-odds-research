import {rankTodayPicks} from '../lib/today-probability-ranking.js';

export default function TodayProbabilityRanking({games,memberships,now,stale,onOpen}) {
  const rows=rankTodayPicks(games,memberships,{now,stale});
  return <section className="my-4 rounded-lg border border-rule p-4" aria-label="오늘의 픽 확률 순위">
    <h2 className="text-base font-semibold">오늘의 픽 · 예상 적중확률 순</h2>
    <p className="mt-1 text-xs text-ink3">오늘 시작 전인 추천 픽만 비교합니다. 개별 경기의 추정 확률이며, 높은 순위가 수익이나 2폴 적중을 보장하지 않습니다.</p>
    {!rows.length ? <p role="status" className="mt-3 text-sm text-ink2">{stale
      ? '데이터 갱신이 지연돼 순위를 표시하지 않습니다.'
      : '현재 순위를 매길 수 있는 시작 전 추천 픽이 없습니다.'}</p>
      : <ol className="mt-3 space-y-2">{rows.map((row,index)=><li key={row.key}>
        <button type="button" className="flex w-full flex-wrap items-center gap-3 rounded-lg border border-rule p-3 text-left hover:bg-paper2"
          aria-haspopup="dialog" aria-label={`${index+1}위 ${row.game.home} 대 ${row.game.away} 경기정보 열기`} onClick={()=>onOpen?.(row.game)}>
          <span className="text-sm font-bold">{index+1}위</span>
          <span className="min-w-0 flex-1 text-sm"><b>{row.game.home} vs {row.game.away}</b>
            <span className="block text-xs text-ink3">{row.game.league} · {new Date(row.kickoff).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false})} KST</span>
            <span className="block">{row.option.market} {row.option.label} · {row.option.선택}</span>
          </span>
          <span className="text-right"><b className="block">{(row.probability*100).toFixed(1)}%</b>
            <span className="block text-xs">배당 {row.odds.toFixed(2)}배</span>
            <span className="block text-xs text-ink3">{row.source}</span>
          </span>
        </button>
      </li>)}</ol>}
  </section>;
}
