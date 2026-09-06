import { useMemo, useState } from "react";
import { Card, Nav } from "../components/ui.jsx";
import { odds } from "../lib/fmt.js";
import { usePolledData } from "../lib/poll.js";
import { recommendedTodayPicks, slipRows } from "../lib/slip.js";

const LIVE_ODDS_URL = "https://proto-odds-collector.fly.dev/api/live-odds";
const PICKS_URL = "https://proto-odds-collector.fly.dev/api/picks";
const TODAY_URL = "https://proto-odds-collector.fly.dev/api/today-recommendations";

const stamp = (value) => {
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", dateStyle: "short", timeStyle: "short" }).format(date)
    : "-";
};

export default function Slip() {
  const { data, at } = usePolledData({
    picks: PICKS_URL,
    liveOdds: LIVE_ODDS_URL,
    today: TODAY_URL,
  }, 60000);
  const [round, setRound] = useState("all");
  const [query, setQuery] = useState("");
  const picks = data.picks;
  const todayPicks = useMemo(() => recommendedTodayPicks(data.today), [data.today]);
  const allRows = useMemo(
    () => slipRows(picks?.live, data.liveOdds, undefined, todayPicks),
    [picks, data.liveOdds, todayPicks],
  );
  const rounds = [...new Set(allRows.map((row) => String(row.round)))];
  const normalized = query.trim().toLowerCase();
  const rows = allRows.filter((row) =>
    (round === "all" || String(row.round) === round) &&
    (!normalized || `${row.number} ${row.home} ${row.away} ${row.market} ${row.label}`.toLowerCase().includes(normalized)));

  return (
    <div className="mx-auto max-w-[1180px] px-5 pb-20">
      <Nav current="slip.html" />
      <header className="market-header">
        <div>
          <h1>프로토 번호·배당표</h1>
          <p>승부식 용지에 옮길 게임번호와 현재 배당, 추천 선택을 한 표에서 대조합니다.</p>
        </div>
        <div className="market-meta">{allRows.length}개 게임번호 · 배당 갱신 {stamp(data.liveOdds?.generated_at)} KST</div>
      </header>

      <Card className="mb-3 flex flex-wrap items-end gap-3 p-4 print:hidden">
        <label className="text-[12px] text-ink3">회차
          <select className="mt-1 block rounded-md border border-rule bg-panel px-3 py-2 text-ink" value={round} onChange={(e) => setRound(e.target.value)}>
            <option value="all">전체 회차</option>
            {rounds.map((value) => <option key={value} value={value}>{value}회</option>)}
          </select>
        </label>
        <label className="min-w-[220px] flex-1 text-[12px] text-ink3">번호·팀·마켓 검색
          <input className="mt-1 block w-full rounded-md border border-rule bg-panel px-3 py-2 text-ink" value={query}
            onChange={(e) => setQuery(e.target.value)} placeholder="예: 102, 두산, 승무패" />
        </label>
        <button type="button" className="rounded-md border border-rule bg-panel px-4 py-2 text-[13px] text-ink" onClick={() => window.print()}>인쇄</button>
      </Card>

      <Card className="overflow-x-auto p-4">
        {!at ? <p className="py-7 text-center text-ink3">불러오는 중…</p> : rows.length === 0 ?
          <p className="py-7 text-center text-ink3">조건에 맞는 발매 경기 배당이 없습니다.</p> :
          <table className="w-full min-w-[760px] border-collapse text-[12.5px]">
            <caption className="sr-only">프로토 승부식 게임번호와 최신 배당</caption>
            <thead><tr className="text-left text-[11px] text-ink3">
              <th className="border-b border-rule2 p-2 text-right">번호</th>
              <th className="border-b border-rule2 p-2">회차·시간</th>
              <th className="border-b border-rule2 p-2">경기</th>
              <th className="border-b border-rule2 p-2">마켓</th>
              <th className="border-b border-rule2 p-2">선택·배당</th>
            </tr></thead>
            <tbody>{rows.map((row) => <tr key={row.key} className={row.selections.some((selection) => selection.recommended) ? "bg-signal/5" : ""}>
              <td className="tnum border-b border-rule2 p-2 text-right text-[16px] font-semibold">{row.number}</td>
              <td className="border-b border-rule2 p-2 whitespace-nowrap"><b>{row.round}회</b><br /><span className="text-ink3">{row.date}</span></td>
              <td className="border-b border-rule2 p-2"><b>{row.home}</b><span className="mx-1.5 text-ink3">vs</span><b>{row.away}</b></td>
              <td className="border-b border-rule2 p-2 whitespace-nowrap">{row.market}{row.label ? <><br /><span className="text-ink3">{row.label}</span></> : null}</td>
              <td className="border-b border-rule2 p-2"><div className="flex flex-wrap gap-2">{row.selections.map((selection) =>
                <span key={selection.name} className={`inline-flex min-w-[92px] justify-between gap-3 rounded-md border px-2.5 py-1.5 ${selection.recommended ? "border-signal bg-signal text-white ring-2 ring-signal/20" : "border-rule"}`}
                  title={selection.recommended ? "오늘의 베팅 추천 선택" : undefined}>
                  <span>{selection.recommended ? "★ " : ""}{selection.name}</span><b className="tnum">{odds(selection.value)}</b>
                </span>)}</div></td>
            </tr>)}</tbody>
          </table>}
      </Card>
      <p className="mt-3 text-[11px] text-ink3">승부식 전용 · 실제 구매 전 지류와 판매점 단말의 번호·배당을 마지막으로 대조하세요.</p>

    </div>
  );
}
