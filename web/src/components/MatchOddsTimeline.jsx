import { useMemo, useState } from "react";
import { marketHistoryGroups, matchChangeTime, matchTimelineEvents } from "../lib/match-changes.js";

function OddsPlot({ group, side, events }) {
  const samples = group.points.map(point => point.odds[side]);
  const low = Math.min(...samples), high = Math.max(...samples);
  const padding = Math.max(0.05, (high - low) * 0.12);
  const first = Math.min(group.points[0].time, ...events.map(event => event.time));
  const last = Math.max(group.points.at(-1).time, ...events.map(event => event.time));
  const x = time => first === last ? 270 : 56 + (time - first) / (last - first) * 428;
  const y = value => 130 - (value - low + padding) / (high - low + 2 * padding) * 104;
  return <>
    <svg className="match-odds-plot" viewBox="0 0 520 161" role="img"
      aria-label={`${group.market} ${group.points[0].names[side]} 배당 관측점. 정확한 배당과 시각은 기록 표에서 확인할 수 있습니다.`}>
      <path d="M56 22 V130 H484" className="match-odds-axis" />
      {[...new Set([high, low])].map(value => <text key={value} x="49" y={y(value) + 4} textAnchor="end">{value.toFixed(2)}</text>)}
      {events.map(event => <line key={event.kind} x1={x(event.time)} x2={x(event.time)} y1="22" y2="130"
        className="match-odds-milestone"><title>{event.label} · {matchChangeTime(event.time)}</title></line>)}
      {group.segments.map((segment, i) => <g key={i} className={`match-odds-series is-${i % 2}`}>
        {segment.points.map((point, j) => {
          const px = x(point.time), py = y(point.odds[side]);
          const label = `${matchChangeTime(point.time)} · ${point.label || group.market} · ${point.odds[side].toFixed(2)}배`;
          return i % 2 === 0
            ? <circle key={j} cx={px} cy={py} r="4"><title>{label}</title></circle>
            : <rect key={j} x={px - 4} y={py - 4} width="8" height="8"><title>{label}</title></rect>;
        })}
      </g>)}
      <text x="56" y="150">{matchChangeTime(first)}</text>
      {first !== last && <text x="484" y="150" textAnchor="end">{matchChangeTime(last)}</text>}
    </svg>
    {group.segments.length > 1 && <p className="match-odds-key">
      {group.segments.map((segment, i) => <span key={i}>{i % 2 === 0 ? "●" : "□"} {segment.label || group.market}</span>)}
    </p>}
  </>;
}

export default function MatchOddsTimeline({ game, change }) {
  const groups = useMemo(() => marketHistoryGroups(game._marketHistory), [game._marketHistory]);
  const events = useMemo(() => matchTimelineEvents(game), [game]);
  const [product, setProduct] = useState(change?.product || "");
  const [side, setSide] = useState(change?.side ?? 0);
  const [showTable, setShowTable] = useState(false);
  const group = groups.find(entry => entry.key === product)
    || groups.find(entry => entry.segments.length > 1)
    || groups.find(entry => entry.points.length > 1) || groups[0];
  const comparableSides = group?.points.every(point => point.names.length === group.points[0].names.length);
  const shownSide = side < (group?.points[0].names.length || 0) ? side : 0;
  return <section className="match-odds-timeline" aria-label="배당 변화 타임라인">
    <h3>배당 변화 타임라인</h3>
    {group ? <>
      <div className="match-odds-controls">
        <label>상품 <select value={group.key} onChange={event => { setProduct(event.target.value); setSide(0); }}>
          {groups.map(entry => <option key={entry.key} value={entry.key}>{entry.gameNo} · {entry.market}</option>)}
        </select></label>
        {comparableSides && <label>선택 <select value={shownSide} onChange={event => setSide(Number(event.target.value))}>
          {group.points[0].names.map((name, index) => <option key={name} value={index}>{name}</option>)}
        </select></label>}
      </div>
      {comparableSides ? <OddsPlot group={group} side={shownSide} events={events} />
        : <p>선택지 구성이 바뀐 상품입니다. 아래 기록에서 시점별 배당을 확인할 수 있습니다.</p>}
      <p className="match-odds-note">한국 시간 · 저장된 배당 시점만 표시합니다. 기준점이 달라지면 별도 구간으로 구분합니다.</p>
      <details onToggle={event => setShowTable(event.currentTarget.open)}>
        <summary>시점별 배당 기록 {group.points.length}건</summary>
        {showTable && <ul className="match-odds-observations">
          {group.segments.map((segment, index) => <li key={index}>
            <b>{segment.label || group.market}</b>
            <ul>{segment.points.map((point, i) => <li key={i}>
              <time dateTime={new Date(point.time).toISOString()}>{matchChangeTime(point.time)}</time>
              <span>{point.names.map((name, j) => `${name} ${point.odds[j].toFixed(2)}배`).join(" · ")}</span>
            </li>)}</ul>
          </li>)}
        </ul>}
      </details>
    </> : <p className="match-odds-note">이 경기의 시점별 배당 기록이 없습니다. 현재 확인 가능한 배당은 아래 상품 목록에 표시됩니다.</p>}
    {events.length > 0 && <ol className="match-odds-events" aria-label="경기와 사전 기록 시점">
      {events.map(event => <li key={event.kind}>
        <time dateTime={new Date(event.time).toISOString()}>{matchChangeTime(event.time)}</time>
        <span>{event.label}</span>
      </li>)}
    </ol>}
  </section>;
}
