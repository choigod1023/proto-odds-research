import { useEffect, useMemo, useState } from "react";
import { Card, GradeBadge, Nav, OddsChip, SectionTitle, Stat } from "../components/ui.jsx";
import { day, formLine, gcls, gradeOf, hhmm, lessBadPick, odds, pct, sgn } from "../lib/fmt.js";

const J = (p) => fetch(`data/${p}?${Date.now()}`).then((r) => r.json());

export default function Markets() {
  const [d, setD] = useState(null);      // picks_v2
  const [grades, setGrades] = useState(null);
  const [combo, setCombo] = useState(null);
  const [today, setToday] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    Promise.all([
      J("picks_v2.json"),
      J("loss_grades.json").catch(() => null),
      J("combo.json").catch(() => null),
      J("today_combo.json").catch(() => null),
    ])
      .then(([a, b, c, e]) => { setD(a); setGrades(b); setCombo(c); setToday(e); })
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <Shell><Empty>데이터를 불러오지 못했습니다: {err}</Empty></Shell>;
  if (!d) return <Shell><Empty>불러오는 중…</Empty></Shell>;

  return (
    <Shell meta={metaLine(d)}>
      <TodayPlan today={today} combo={combo} grades={grades} />
      <GameList data={d} grades={grades} />
      <Evidence grades={grades} tally={d.tally} />
    </Shell>
  );
}

const metaLine = (d) =>
  `${(d.live || []).length + (d.past || []).length}경기 · 회차 ${(d.rounds || []).join(", ")}` +
  ` · 갱신 ${String(d.generated_at || "").slice(0, 16).replace("T", " ")}`;

function Shell({ children, meta }) {
  return (
    <div className="mx-auto max-w-[960px] px-5 pb-20">
      <Nav current="markets.html" />
      <header>
        <h1 className="mt-[22px] mb-1 text-[22px] tracking-[-.01em]">경기 분석</h1>
        <p className="m-0 text-[13.5px] text-ink2">
          발매 중인 전 마켓을 <b>과거 실측 수익률</b>로 등급화한다. 예측이 아니라{" "}
          <b>덜 잃는 선택</b>이다.
        </p>
        {meta && <div className="mt-1.5 text-[11.5px] text-ink3">{meta}</div>}
      </header>
      {children}
    </div>
  );
}

const Empty = ({ children }) => (
  <div className="py-7 text-center text-[13px] text-ink3">{children}</div>
);

/* ── ① 오늘 살 것 ──────────────────────────────────────────────── */
function TodayPlan({ today, combo, grades }) {
  const plans = useMemo(() => (today?.plans || []).filter((p) => p.ok), [today]);
  const solo = today?.solo || null;
  const [i, setI] = useState(0);

  if (!plans.length && !solo) return null;
  const p = i < 0 ? null : plans[i];
  const bl = (combo?.baseline || []).find((x) => x.legs === 2);
  const A = (grades?.odds_bins || []).find((x) => x.grade === "A");

  return (
    <Card className="mt-[18px] px-[18px] py-4">
      <div className="text-[12px] tracking-[.02em] text-ink3">오늘 살 거면</div>

      <div className="my-2.5 mb-3.5 flex flex-wrap gap-1.5">
        {solo && <Tab on={i < 0} onClick={() => setI(-1)}>단폴</Tab>}
        {plans.map((q, k) => (
          <Tab key={q.target} on={k === i} onClick={() => setI(k)}>
            {q.target}배
            <span className={`tnum text-[11px] ${k === i ? "text-sev3" : "text-ink3"}`}>
              {(q.expected_roi * 100).toFixed(0)}%
            </span>
          </Tab>
        ))}
      </div>

      <div className="flex flex-wrap gap-x-7 gap-y-2 border-b border-rule2 pb-3">
        {p ? (
          <>
            <Stat k="적중 확률" v={`${(p.hit_est * 100).toFixed(1)}%`} />
            <Stat k="실배당" v={`${p.actual_odds.toFixed(2)}×`} />
            <Stat k="기대 수익률" v={`${(p.expected_roi * 100).toFixed(1)}%`} tone="sev" />
            <Stat k="구성" v={`${p.legs}폴`} />
          </>
        ) : (
          <>
            <Stat k="배당" v={odds(solo.odds)} />
            <Stat k="환급률" v={`${solo.payout}%`} />
            <Stat k="구매 조건" v="'한경기' 지정만" />
          </>
        )}
      </div>

      <div className="mt-3">
        {(p ? p.picks : [solo]).map((c, k) => <Leg key={k} c={c} />)}
      </div>

      {bl && (
        <div className="mt-3 border-t border-rule2 pt-[11px] text-[12px] leading-[1.75] text-ink2">
          <b className="text-ink">다리를 줄이는 게 제일 크다.</b>{" "}
          같은 선택도 1폴 <b className="tnum text-ink">−9.8%</b> ·
          2폴 <b className="tnum text-ink">−18.7%</b> · 3폴 <b className="tnum">−26.7%</b> —
          다리 하나에 <b className="text-ink">8.9%p</b> 다. 배당대를 고르는 이득(3.9%p)보다 크다.
          단폴은 '한경기' 지정 경기만 되니, 되면 그걸 먼저 본다.<br />
          아무 2폴 <b className="tnum">{(bl.any * 100).toFixed(1)}%</b> →{" "}
          배당 {A ? A.bin : "1.0-1.3"} 로 짠 2폴{" "}
          <b className="tnum">{(bl.best * 100).toFixed(1)}%</b>.
          전부 마이너스다. 목표 배당을 올릴수록 더 잃는다.
        </div>
      )}
    </Card>
  );
}

const Tab = ({ on, onClick, children }) => (
  <button
    onClick={onClick}
    className={`flex items-center gap-1.5 rounded-full border px-[13px] py-1.5 text-[12px] leading-none ${
      on ? "border-ink font-semibold text-ink" : "border-rule text-ink2 hover:border-ink3"
    }`}
  >
    {children}
  </button>
);

const Leg = ({ c }) => (
  <div className="flex flex-wrap items-baseline gap-2.5 border-t border-rule2 py-[7px] text-[13px] first:border-t-0">
    <span className="tnum min-w-[38px] text-[11.5px] text-ink3">{hhmm(c.date)}</span>
    <span className="rounded border border-rule px-[5px] py-px text-[10.5px] text-ink3">
      {c.league}
    </span>
    <span className="min-w-[170px] flex-1">
      {c.match} — <b>{c.market}{c.market_label ? ` ${c.market_label}` : ""} {c.sel}</b>
    </span>
    <span className="tnum font-semibold">{odds(c.odds)}</span>
    {c.beats && (
      <span className="text-[10.5px] text-signal">
        {c.round}회차가 유리 ({c.beats.round}회차 {odds(c.beats.odds)})
      </span>
    )}
  </div>
);

/* ── ② 경기 ───────────────────────────────────────────────────── */
const STATUS = [
  ["예정", "예정"], ["경기전", "배당 나옴"], ["배당대기", "배당 대기"],
  ["정산", "정산"], ["", "전체"],
];

function GameList({ data, grades }) {
  const [f, setF] = useState({ st: "예정", lg: "", mk: "", rd: "", q: "" });
  const [showModel, setShowModel] = useState(false);
  const pool = useMemo(() => [...(data.live || []), ...(data.past || [])], [data]);

  const uniq = (a) => [...new Set(a)].filter((v) => v != null && v !== "");
  const leagues = useMemo(() => uniq(pool.map((g) => g.league)).sort(), [pool]);
  const markets = useMemo(
    () => uniq(pool.flatMap((g) => (g.options || []).map((o) => o.market))).sort(), [pool]);
  const rounds = useMemo(() => uniq(pool.map((g) => g.round)).sort((a, b) => b - a), [pool]);

  const games = useMemo(() => {
    const q = f.q.trim().toLowerCase();
    return pool.filter((g) =>
      (!f.st || (f.st === "예정"
        ? g.status === "경기전" || g.status === "배당대기"
        : g.status === f.st)) &&
      (!f.lg || g.league === f.lg) &&
      (!f.rd || String(g.round) === f.rd) &&
      (!q || [g.home, g.away, g.league].join(" ").toLowerCase().includes(q)));
  }, [pool, f]);

  const rows = [];
  let cur = null, n = 0;
  for (const g of games) {
    const opts = (g.options || []).filter((o) => !f.mk || o.market === f.mk);
    const wait = g.status === "배당대기";
    if (!opts.length && !wait) continue;
    if (wait && f.mk) continue;
    n++;
    const key = `${g.league} · ${day(g.date)}`;
    if (key !== cur) {
      cur = key;
      rows.push(<div key={`h${key}${n}`} className="mt-[18px] mb-1.5 text-[11px] font-semibold tracking-[.03em] text-ink3">{key}</div>);
    }
    rows.push(<Game key={`${g.league}${g.home}${g.away}${g.date}${n}`} g={g} opts={opts} wait={wait} grades={grades} />);
  }

  const sel = "rounded-md border border-rule bg-panel px-[7px] py-1 text-[12px] text-ink";
  return (
    <>
      <SectionTitle note={`${n}경기`}>경기</SectionTitle>
      <div className={`sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-rule bg-paper py-2.5 text-[12px] text-ink2 ${showModel ? "show-model" : ""}`}>
        <label className="flex items-center gap-1.5">상태
          <select className={sel} value={f.st} onChange={(e) => setF({ ...f, st: e.target.value })}>
            {STATUS.map(([v, l]) => <option key={l} value={v}>{l}</option>)}
          </select></label>
        <Sel label="리그" v={f.lg} opts={leagues} on={(v) => setF({ ...f, lg: v })} cls={sel} />
        <Sel label="마켓" v={f.mk} opts={markets} on={(v) => setF({ ...f, mk: v })} cls={sel} />
        <Sel label="회차" v={f.rd} opts={rounds} on={(v) => setF({ ...f, rd: v })} cls={sel} suffix="회차" />
        <input type="search" placeholder="팀 검색" value={f.q}
          onChange={(e) => setF({ ...f, q: e.target.value })}
          className={`${sel} w-[120px]`} />
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showModel} onChange={(e) => setShowModel(e.target.checked)} />
          모델 수치
        </label>
      </div>
      <div className={showModel ? "show-model" : ""}>
        {rows.length ? rows : <Empty>조건에 맞는 경기가 없다</Empty>}
      </div>
    </>
  );
}

const Sel = ({ label, v, opts, on, cls, suffix = "" }) => (
  <label className="flex items-center gap-1.5">{label}
    <select className={cls} value={v} onChange={(e) => on(e.target.value)}>
      <option value="">전체</option>
      {opts.map((o) => <option key={o} value={o}>{o}{suffix}</option>)}
    </select>
  </label>
);

function Game({ g, opts, wait, grades }) {
  // 같은 마켓의 두 선택지가 같은 등급이면 '=' — 어느 쪽을 사도 같아 고를 근거가 없다
  const tie = useMemo(() => {
    const by = {}, t = {};
    opts.forEach((o) => {
      const k = o.market + (o.label || "");
      (by[k] = by[k] || []).push(gradeOf(grades, o["배당"])?.grade);
    });
    Object.entries(by).forEach(([k, v]) => { if (v.length === 2 && v[0] && v[0] === v[1]) t[k] = true; });
    return t;
  }, [opts, grades]);

  const head = opts.filter((o) => o.market === "승패" || o.market === "승무패");
  const shown = (head.length ? head : opts).slice(0, 3);
  // 경기별 픽 — 모델이 아니라 **실측 등급**으로 고른다 (모델 추천은 −42.2% 였다)
  const pick = wait ? null : lessBadPick(grades, opts);

  return (
    <Card as="details" className="my-1.5">
      <summary className="flex cursor-pointer flex-wrap items-center gap-[11px] px-3 py-2.5">
        <span className="tnum min-w-10 text-[11.5px] text-ink3">{hhmm(g.date)}</span>
        <span className="min-w-[160px] flex-1 text-[13.5px] font-semibold">
          {g.home} <span className="text-[12px] font-normal text-ink3">vs</span> {g.away}
        </span>
        <span className="flex gap-1.5">
          {wait ? <OddsChip label="배당" value="대기" />
            : shown.map((o, k) => {
                const gr = gradeOf(grades, o["배당"]);
                return <OddsChip key={k} label={o["선택"]} value={odds(o["배당"])}
                  grade={gr ? gcls(gr.grade) : "U"}
                  title={gr ? `배당 ${gr.bin} 실측 ${(gr.roi * 100).toFixed(1)}%` : "등급 없음"} />;
              })}
        </span>
        {pick && (
          <span
            title={pick.tie
              ? `최선 등급(${pick.g.bin})이 여럿이라 고를 근거가 없다`
              : `배당 ${pick.g.bin} — 과거 적중 ${((pick.g.hit ?? 0) * 100).toFixed(1)}% · 수익률 ${(pick.g.roi * 100).toFixed(1)}%`}
            className={`whitespace-nowrap rounded px-2 py-[3px] text-[11px] font-semibold ${
              pick.tie
                ? "border border-dashed border-ink3 text-ink3"
                : "bg-ink text-paper"
            }`}
          >
            {pick.tie ? (
              <>고를 근거 없음</>
            ) : (
              <>{pick.o["선택"]} <span className="tnum">{odds(pick.o["배당"])}</span>
                {pick.g.hit != null && (
                  <> · 적중 <b className="tnum text-ink">
                    {(pick.g.hit * 100).toFixed(0)}%</b></>)}
                {pick.why && <span className="text-ink3"> · {pick.why}</span>}</>
            )}
          </span>
        )}
        <span className="whitespace-nowrap text-[10.5px] text-ink3">
          {g.round}회차{g["판단"] ? ` · ${g["판단"]}` : ""}
        </span>
      </summary>
      <div className="px-3 pb-3 pt-2.5">
        {wait ? (
          <div className="rounded-[7px] border border-dashed border-rule px-2.5 py-2 text-[12px] text-ink3">
            배당이 아직 발표되지 않았다. 프로토는 <b>경기 목록을 먼저 열고 배당을 나중에 붙인다.</b>
          </div>
        ) : <OptTable opts={opts} grades={grades} tie={tie} pick={pick} model={g["추천"]} />}
        <Why g={g} />
      </div>
    </Card>
  );
}

function OptTable({ opts, grades, tie, pick, model }) {
  const th = "border-b border-rule2 pb-[5px] pr-2 text-left text-[11px] font-medium text-ink3";
  const td = "border-b border-rule2 py-[5px] pr-2 align-baseline";
  return (
    <table className="w-full border-collapse text-[12.5px]">
      <thead><tr>
        <th className={th}>마켓 / 선택</th>
        <th className={`${th} text-right`}>배당</th>
        <th className={`${th} text-right`}>과거 적중</th>
        <th className={`${th} model-col text-right`}>시장</th>
        <th className={`${th} model-col text-right`}>모델</th>
        <th className={`${th} model-col text-right`}>기대</th>
        <th className={th}>판단</th>
      </tr></thead>
      <tbody>
        {opts.map((o, k) => {
          const gr = gradeOf(grades, o["배당"]);
          const t = tie[o.market + (o.label || "")];
          return (
            <tr key={k}>
              <td className={td}>
                {gr && <GradeBadge grade={t ? "T" : gcls(gr.grade)}
                  title={t ? `양쪽이 같은 배당대(${gr.bin}) — 고를 근거가 없다`
                           : `배당 ${gr.bin} 실측 ${(gr.roi * 100).toFixed(1)}%`} />}
                {o.market}{o.label ? ` ${o.label}` : ""} · {o["선택"]}
                {o["적중"] === true ? " ✔" : o["적중"] === false ? " ✕" : ""}
              </td>
              <td className={`${td} tnum text-right`}>{odds(o["배당"])}</td>
              <td className={`${td} tnum text-right text-ink3`}>
                {gr?.hit != null ? `${(gr.hit * 100).toFixed(0)}%` : "–"}</td>
              <td className={`${td} model-col tnum text-right`}>{pct(o["시장확률"])}</td>
              <td className={`${td} model-col tnum text-right`}>{pct(o["모델확률"])}</td>
              <td className={`${td} model-col tnum text-right`}>{sgn(o["예상손익"])}</td>
              <td className={`${td} text-[11.5px]`}>
                {pick && !pick.tie && pick.o === o && (
                  <span className="text-ink">덜 잃는 쪽</span>)}
                {pick && pick.tie && (gradeOf(grades, o["배당"])?.grade === pick.g.grade) && (
                  <span className="text-ink3">동률 — 고를 근거 없음</span>)}
                {model && model["게임번호"] === o["게임번호"] &&
                 model["선택"] === o["선택"] && model.market === o.market && (
                  <span className="model-col text-sev2"> 모델 최대괴리</span>)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Why({ g }) {
  const f = [];
  const s = g["선발"];
  if (s && (s.home || s.away)) f.push(`선발 ${g.home} ${s.home || "?"} / ${g.away} ${s.away || "?"}`);
  if (g["h2h"]) f.push(g["h2h"]);
  const fh = formLine(g.form_home), fa = formLine(g.form_away);
  if (fh) f.push(`${g.home} ${fh}`);
  if (fa) f.push(`${g.away} ${fa}`);
  if (g.lam_src === "풀링") f.push("λ는 리그 표본을 끌어와 추정 — 컵대회는 모델이 더 부정확하다");
  if (!g["해설"] && !f.length) return null;
  // 결론 문장(첫 마침표까지)을 떼어 굵게 — 판단이 본문에 묻히면 안 된다
  const txt = g["해설"] || "";
  const cut = txt.indexOf(". ");
  const verdict = cut > 0 ? txt.slice(0, cut + 1) : txt;
  const rest = cut > 0 ? txt.slice(cut + 2) : "";
  return (
    <div className="mt-2.5 border-t border-rule2 pt-2.5 text-[12.5px] leading-[1.75] text-ink2">
      {verdict && <b className="text-ink">{verdict}</b>} {rest}
      {!!f.length && <div className="mt-1.5 text-[11.5px] text-ink3">{f.join(" · ")}</div>}
    </div>
  );
}

/* ── ③ 근거 ───────────────────────────────────────────────────── */
function Evidence({ grades, tally }) {
  const b = grades?.basis;
  const items = [
    ["배당대 등급",
      b ? `실측 ${b.n_selections.toLocaleString()}건 · ${b.years[0]}~${b.years.at(-1)}. 네 해 모두 같은 방향일 때만 등급을 준다.`
        : "실측 수익률로 A~D 를 매긴다."],
    ["조합 산술",
      "다리를 하나 더 붙일 때마다 마진이 한 번 더 물린다 — 약 −6%p. 목표 배당은 다리 수가 아니라 다리당 배당으로 맞춘다."],
    ["왜 이길 수 없나",
      "12개 검증 기록. 필요한 우위 6.8%p, 동원 가능한 정보 4.8%p."],
  ];
  return (
    <>
      <SectionTitle note="숫자의 출처">근거</SectionTitle>
      <div className="grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(230px,1fr))]">
        {items.map(([h, dsc]) => (
          <Card key={h}>
            <a href="research.html" className="block px-[15px] py-[13px] text-inherit no-underline">
              <div className="mb-[3px] text-[13px] font-semibold">{h}</div>
              <div className="text-[11.5px] leading-[1.65] text-ink3">{dsc}</div>
            </a>
          </Card>
        ))}
      </div>
      {tally && (
        <div className="mt-3 text-[11.5px] leading-[1.75] text-ink3">
          모델 수치는 <b className="text-sev3">참고용이다.</b> 이 페이지의 모델 추천을 그대로 따랐다면
          정산 {tally.n}건에서 적중 {(tally.hit_rate * 100).toFixed(1)}%,
          수익률 <b className="text-sev3">{(tally.roi * 100).toFixed(1)}%</b> 였다 —
          아무거나 살 때(−13.7%)보다 나쁘다. 모델이 시장보다 부정확해서,
          격차가 큰 곳은 곧 모델이 크게 틀린 곳이다.
        </div>
      )}
    </>
  );
}
