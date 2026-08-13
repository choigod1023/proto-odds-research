import { useEffect, useMemo, useState } from "react";
import { Card, GradeBadge, Nav, OddsChip, SectionTitle, Stat } from "../components/ui.jsx";
import { day, dayTag, formLine, gcls, gradeOf, hhmm, kstMMDD, lessBadPick, odds, pct, sgn } from "../lib/fmt.js";
import { usePolledData } from "../lib/poll.js";

// 실시간 점수만 **수집 머신이 직접 서빙**한다.
// 나머지 산출물(docs/data/*.json)은 git push 로 나르는데 그 주기가 30분이라
// 실시간이 될 수 없다. 3분마다 커밋하면 하루 300커밋이라 레포가 망가지고,
// 브라우저가 네이버 API 를 직접 부르는 건 CORS 로 막힌다. 그래서 이 파일만 별도 경로다.
const LIVE_URL = "https://proto-odds-collector.fly.dev/live_scores.json";

// 배당도 같은 처리다. picks_v2.json 의 배당은 산출물 갱신 때 굳으므로 최대 한 시간
// 낡는다 — 2026-08-13 실측 231건 중 73건(32%)이 원천과 달랐다. 5분마다 갱신되는
// 이 파일로 덮어쓴다.
const ODDS_URL = "https://proto-odds-collector.fly.dev/live_odds.json";

/** 주기적으로 JSON 하나를 받는다. 실패하면 조용히 넘어간다 — 사이트는 그대로 동작. */
function usePoll(url, ms) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let stop = false;
    const load = () =>
      fetch(`${url}?${Date.now()}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (!stop && d) setData(d); })
        .catch(() => {});
    load();
    const t = setInterval(load, ms);
    return () => { stop = true; clearInterval(t); };
  }, [url, ms]);
  return data;
}

const useLive = () => usePoll(LIVE_URL, 60000);
const useLiveOdds = () => usePoll(ODDS_URL, 120000);

/**
 * picks_v2 의 배당 위에 실시간 배당을 덮어쓴다.
 *
 * ⚠️ 실시간 쪽에 값이 **있을 때만** 갈아끼운다. 없다고 지우면 멀쩡한 값을
 *    빈 칸으로 바꿔 오히려 나빠진다(아직 배당이 안 붙은 회차가 늘 섞여 있다).
 * ⚠️ 배당이 바뀌면 등급·판단도 같이 바뀌어야 하므로, 화면은 이 값을 기준으로
 *    다시 계산한다. 모델확률·시장확률은 산출 시점 값이라 건드리지 않는다.
 */
function withLiveOdds(games, lo) {
  if (!lo?.odds) return games;
  return games.map((g) => {
    const bucket = lo.odds[String(g.round)];
    if (!bucket) return g;
    // ⚠️ 위치로 맞춘다. picks_v2 의 옵션에는 선택지 인덱스가 없고, '선택'(홈/무/원정/
    //    언더/오버/홀/짝…)을 배당 배열의 자리로 옮기는 규칙은 마켓마다 달라 깨지기 쉽다.
    //    같은 게임번호 안에서 옵션 순서는 원천 배당 배열 순서와 동일하므로,
    //    게임번호별로 묶어 n번째끼리 대응시킨다.
    const seen = {};
    let touched = false;
    const options = (g.options || []).map((o) => {
      const gn = String(o["게임번호"]);
      const i = (seen[gn] = (seen[gn] ?? -1) + 1);
      const fresh = bucket[gn];
      if (!fresh || i >= fresh.length) return o;
      const v = fresh[i];
      if (v == null || v === o["배당"]) return o;
      touched = true;
      return { ...o, 배당: v, _live: true };
    });
    return touched ? { ...g, options } : g;
  });
}

/** 프로토 표기와 네이버 표기가 다르므로(마이말린 ↔ 마이애미) 별칭까지 키로 넣는다.
 *
 * ⚠️ 키에 **날짜(MM.DD)를 반드시 포함**한다. 팀 조합만으로 잡으면 MLB 3~4연전에서
 *    어제 경기와 오늘 경기가 뭉개진다 — 정산 경기 55건 중 37건이 어긋났었다. */
function buildLiveIndex(live) {
  const m = new Map();
  (live?.games || []).forEach((g) => {
    const hs = [g.home, ...(g.home_alias || [])].filter(Boolean);
    const as = [g.away, ...(g.away_alias || [])].filter(Boolean);
    hs.forEach((h) => as.forEach((a) => m.set(`${h}|${a}|${g.md}`, g)));
  });
  return m;
}

export default function Markets() {
  // ⚠️ 예전엔 처음 한 번만 fetch 했다. 수집기는 30분마다 새 JSON 을 올리는데
  //    화면이 첫 로드에 멈춰 있어 새로고침을 눌러야만 바뀌었다. 이제 스스로 갱신한다.
  const { data, at } = usePolledData({
    d: "data/picks_v2.json",
    grades: "data/loss_grades.json",
    combo: "data/combo.json",
    today: "data/today_combo.json",
  }, 300000);   // 5분
  const { d, grades, combo, today } = data;

  if (at && !d) return <Shell><Empty>데이터를 불러오지 못했습니다</Empty></Shell>;
  if (!d) return <Shell><Empty>불러오는 중…</Empty></Shell>;

  return (
    <Shell meta={metaLine(d, at)}>
      <TodayPlan today={today} combo={combo} grades={grades} />
      <GameList data={d} grades={grades} caps={grades?.odds_caps} />
      <Evidence grades={grades} tally={d.tally} />
    </Shell>
  );
}

/** 진행 중이면 실시간 점수를, 아니면 null. 정산 표시는 기존 로직이 맡는다.
 *  프로토 date 는 '07.31(금) 08:10' 꼴이라 앞 5글자가 MM.DD 다. */
function liveOf(idx, g) {
  const m = idx.get(`${g.home}|${g.away}|${String(g.date || "").slice(0, 5)}`);
  if (!m || m.status === "BEFORE") return null;
  return m;
}

// `갱신` 은 데이터가 만들어진 시각, `확인` 은 브라우저가 마지막으로 받아 본 시각이다.
// 둘을 나눠 적어야 "화면이 멈춘 건지, 서버가 안 만든 건지"가 구분된다.
const metaLine = (d, at) =>
  // ⚠️ '승부식' 을 못박아 적는다. 수집기가 game_category=pt1 을 하드코딩하고 있어
  //    이 사이트의 모든 숫자는 **승부식 전용**이다. 기록식(pt2)은 한 건도 수집한 적이 없다.
  //    안 적으면 보는 사람이 기록식 배당도 여기 있다고 착각한다.
  `${(d.live || []).length + (d.past || []).length}경기 · 회차 ${(d.rounds || []).join(", ")} · 승부식` +
  ` · 갱신 ${String(d.generated_at || "").slice(0, 16).replace("T", " ")}` +
  (at ? ` · 확인 ${new Date(at).toTimeString().slice(0, 5)}` : "");

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
const MODES = [["hit", "적중 우선"], ["roi", "손실 최소"]];
const STATUS = [
  ["예정", "예정"], ["경기전", "배당 나옴"], ["배당대기", "배당 대기"],
  ["정산", "정산"], ["", "전체"],
];

function GameList({ data, grades, caps }) {
  const liveFeed = useLive();
  const liveOdds = useLiveOdds();
  const lidx = useMemo(() => buildLiveIndex(liveFeed), [liveFeed]);
  // ⚠️ 날짜 기본값은 **오늘**이다. 전체로 두면 목록이 미래 경기로 뒤덮인다 —
  //    2026-08-13 실측: 예정 189건 중 165건(87%)이 아직 배당도 안 나온 8/14 이후
  //    경기였고, 정작 오늘 살 수 있는 6건이 그 속에 묻혔다. 스크롤하면
  //    '배당 대기'만 줄줄이 보여서 "오늘 건데 왜 배당이 없냐"로 읽힌다.
  //    이 페이지의 제목이 '오늘 뭘 사면 덜 잃나' 다. 기본값이 그걸 보여줘야 한다.
  const [f, setF] = useState({ st: "예정", lg: "", mk: "", rd: "", q: "",
                               dt: kstMMDD(0) });
  const [showModel, setShowModel] = useState(false);
  // 적중 우선 / 손실 최소 — 두 목표가 갈린다.
  // ⚠️ 수치를 여기 적지 않는다. loss_grades.json 의 pick_modes 를 읽는다 —
  //    예전엔 손으로 박아뒀다가 파이프라인을 고친 뒤 조용히 틀린 값이 남았다.
  const [mode, setMode] = useState("hit");
  // ⚠️ 적중률을 올리는 지렛대는 '뭘 고르나' 가 아니라 **'어느 경기를 버리나'** 다.
  //    실측: 전부 65.9% → 최저배당 ≤1.3 인 경기만 77.6%. ROI 도 같이 좋아진다.
  const [cap, setCap] = useState(0);          // 0 = 제한 없음
  // 실시간 배당을 덮어쓴 뒤에 필터·등급 계산으로 넘긴다. 배당이 바뀌면
  // 등급·'덜 잃는 쪽' 판정도 같이 바뀌어야 하므로 반드시 이 지점에서 갈아끼운다.
  const pool = useMemo(
    () => withLiveOdds([...(data.live || []), ...(data.past || [])], liveOdds),
    [data, liveOdds]);

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
      // 날짜 — 회차는 여러 날에 걸쳐 있어서(93회차만 08.07~08.10) 회차 필터로는
      // '오늘 살 수 있는 것'을 못 고른다. 경기일로 직접 거른다.
      (!f.dt || String(g.date ?? "").slice(0, 5) === f.dt) &&
      (!q || [g.home, g.away, g.league].join(" ").toLowerCase().includes(q)));
  }, [pool, f]);

  const rows = [];
  let cur = null, n = 0;
  // 화면에 실제로 뜬 픽의 성적 — 정산된 경기만. 필터·모드·상한을 그대로 반영한다.
  const live = { n: 0, hit: 0, ret: 0 };
  for (const g of games) {
    const opts = (g.options || []).filter((o) => !f.mk || o.market === f.mk);
    const wait = g.status === "배당대기";
    if (!opts.length && !wait) continue;
    if (wait && f.mk) continue;
    if (cap && wait) continue;      // 배당이 없으면 상한을 적용할 수 없다
    // 최저배당 상한 — 강한 favorite 이 없는 경기는 버린다
    // ⚠️ n++ 를 이 필터 **앞**에 두면 헤더의 '몇 경기' 가 버린 경기까지 센다.
    if (cap) {
      const lo = Math.min(...opts.map((o) => o["배당"]).filter((x) => x > 0));
      if (!(lo <= cap)) continue;
    }
    n++;
    if (g.status === "정산") {
      const pk = lessBadPick(grades, opts, mode);
      const h = pk && !pk.tie ? pk.o["적중"] : null;
      if (h !== null && h !== undefined) {
        live.n++; live.hit += h ? 1 : 0;
        live.ret += h ? pk.o["배당"] - 1 : -1;
      }
    }
    const key = `${g.league} · ${day(g.date)}`;
    if (key !== cur) {
      cur = key;
      // '오늘/내일' 을 헤더에 박아 둔다. 전체 보기에서도 눈으로 갈리게 —
      // 필터를 걸어야만 구분되면 목록을 훑는 사람에게는 없는 기능이다.
      const tag = dayTag(g.date);
      rows.push(
        <div key={`h${key}${n}`} className="mt-[18px] mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-[.03em] text-ink3">
          {tag && (
            <span className={`rounded-[4px] px-[5px] py-[2px] text-[10px] ${
              tag === "오늘" ? "bg-ink text-paper" : "border border-rule text-ink2"}`}>
              {tag}
            </span>
          )}
          <span>{key}</span>
        </div>);
    }
    rows.push(<Game key={`${g.league}${g.home}${g.away}${g.date}${n}`} g={g} opts={opts} wait={wait} grades={grades} mode={mode} lv={liveOf(lidx, g)} />);
  }

  const sel = "rounded-md border border-rule bg-panel px-[7px] py-1 text-[12px] text-ink";
  const capRow = cap ? (caps || []).find((c) => c.cap === cap) : null;
  return (
    <>
      <SectionTitle note={`${n}경기`}>경기</SectionTitle>
      <div className={`sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-rule bg-paper py-2.5 text-[12px] text-ink2 ${showModel ? "show-model" : ""}`}>
        <label className="flex items-center gap-1.5">상태
          <select className={sel} value={f.st} onChange={(e) => setF({ ...f, st: e.target.value })}>
            {STATUS.map(([v, l]) => <option key={l} value={v}>{l}</option>)}
          </select></label>
        {/* 회차는 여러 날에 걸쳐 있다(93회차만 08.07~08.10). '오늘 뭘 살 수 있나'
            를 보려면 회차가 아니라 경기일로 골라야 한다. */}
        <label className="flex items-center gap-1.5">날짜
          <select className={sel} value={f.dt}
            onChange={(e) => setF({ ...f, dt: e.target.value })}>
            <option value="">전체</option>
            <option value={kstMMDD(0)}>오늘</option>
            <option value={kstMMDD(1)}>내일</option>
          </select></label>
        <Sel label="리그" v={f.lg} opts={leagues} on={(v) => setF({ ...f, lg: v })} cls={sel} />
        <Sel label="마켓" v={f.mk} opts={markets} on={(v) => setF({ ...f, mk: v })} cls={sel} />
        <Sel label="회차" v={f.rd} opts={rounds} on={(v) => setF({ ...f, rd: v })} cls={sel} suffix="회차" />
        <input type="search" placeholder="팀 검색" value={f.q}
          onChange={(e) => setF({ ...f, q: e.target.value })}
          className={`${sel} w-[120px]`} />
        <label className="flex items-center gap-1.5">최저배당
          <select className={sel} value={cap}
            onChange={(e) => setCap(Number(e.target.value))}>
            <option value={0}>제한 없음</option>
            {(caps || []).filter((c) => c.cap).map((c) => (
              <option key={c.cap} value={c.cap}>
                ≤{c.cap} · 적중 {(c.hit * 100).toFixed(0)}%
              </option>))}
          </select></label>
        <label className="flex items-center gap-1.5">픽 기준
          <select className={sel} value={mode} onChange={(e) => setMode(e.target.value)}>
            {MODES.map(([v, label]) => {
              const m = grades?.pick_modes?.[v];
              return <option key={v} value={v}>{label}
                {m ? ` (${(m.hit * 100).toFixed(1)}% · ${(m.roi * 100).toFixed(1)}%)` : ""}
              </option>;
            })}
          </select></label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showModel} onChange={(e) => setShowModel(e.target.checked)} />
          모델 수치
        </label>
      </div>
      {cap > 0 && n < 2 && (
        <p className="mt-2 text-[11.5px] leading-[1.7] text-sev3">
          남은 경기가 {n}개다 — <b>2폴을 만들 수 없다.</b> 조합은 서로 다른 경기가
          최소 둘 필요하다. 상한을 올리거나 다음 회차를 기다려야 한다.
        </p>
      )}
      {capRow && (
        <p className="mt-2 text-[11.5px] leading-[1.7] text-ink3">
          최저배당 ≤{capRow.cap} 인 경기만 산 과거 실측 —
          적중 <b className="tnum text-ink">{(capRow.hit * 100).toFixed(1)}%</b> ·
          ROI <b className="tnum">{(capRow.roi * 100).toFixed(2)}%</b> ·
          2폴 티켓 적중 <b className="tnum">{(capRow.hit2 * 100).toFixed(1)}%</b>
          <span className="opacity-70">
            {" "}(전체 경기의 {(capRow.share * 100).toFixed(0)}% · n={capRow.n.toLocaleString()})
          </span>
        </p>
      )}
      {live.n > 0 && (
        <p className="mt-2 text-[11.5px] leading-[1.7] text-ink3">
          지금 조건에서 <b className="text-ink">끝난 {live.n}경기</b> — 여기 뜬 픽을 그대로 샀다면
          적중 <b className="tnum text-ink">{live.hit}/{live.n}
          ({((live.hit / live.n) * 100).toFixed(1)}%)</b> ·
          수익률 <b className={`tnum ${live.ret < 0 ? "text-sev3" : "text-ink"}`}>
            {((live.ret / live.n) * 100).toFixed(1)}%</b>
          <span className="opacity-70"> · 화면에 보이는 것만 센 값이라 표본이 작다.
            장기 실측은 위의 등급표를 봐라</span>
        </p>
      )}
      <div className={showModel ? "show-model" : ""}>
        {rows.length ? rows : (
          // 날짜 기본값이 '오늘' 이라 심야·비수기엔 빈 화면이 될 수 있다.
          // 그때 '고장' 으로 보이지 않게 다음 행동을 바로 알려 준다.
          <Empty>
            조건에 맞는 경기가 없다
            {f.dt && (
              <> — <button className="underline underline-offset-2"
                onClick={() => setF({ ...f, dt: "" })}>모든 날짜 보기</button></>
            )}
          </Empty>
        )}
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

function Game({ g, opts, wait, grades, mode, lv }) {
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
  const pick = wait ? null : lessBadPick(grades, opts, mode);
  const done = g.status === "정산";
  // 프로토 정산은 경기가 끝나고도 한참 뒤다. 그 사이를 실시간 점수가 메운다.
  const playing = !!lv && !lv.finished;
  // 정산 점수가 있으면 그걸 쓰고(확정), 없으면 실시간 점수로 채운다.
  const score = (done && g.score)
    || (lv && lv.home_score != null && lv.away_score != null
        ? [lv.home_score, lv.away_score] : null);
  // 이 경기에서 우리 픽이 맞았나. 정산 전이면 null.
  const picked = done && pick && !pick.tie ? pick.o["적중"] : null;

  return (
    <Card as="details" className="my-1.5">
      <summary className="flex cursor-pointer flex-wrap items-center gap-[11px] px-3 py-2.5">
        <span className="tnum min-w-10 text-[11.5px] text-ink3">{hhmm(g.date)}</span>
        <span className="min-w-[160px] flex-1 text-[13.5px] font-semibold">
          {g.home}{" "}
          {score
            ? <span className="tnum text-[13px]" title={g["결과"] || ""}>
                <b className={score[0] > score[1] ? "" : "font-normal text-ink3"}>{score[0]}</b>
                <span className="px-[3px] font-normal text-ink3">:</span>
                <b className={score[1] > score[0] ? "" : "font-normal text-ink3"}>{score[1]}</b>
              </span>
            : <span className="text-[12px] font-normal text-ink3">vs</span>}{" "}
          {g.away}
        </span>
        {/* 색을 새로 만들지 않는다 — 테마에서 색은 값의 심각도(sev)와 구조(signal)
            전용이고 '진행 중'은 둘 다 아니다. 대신 배지에 '3회초' 처럼 어디까지
            왔는지를 그대로 찍어, 라벨 자체로 '예정'과 구분되게 한다. */}
        <span className={`whitespace-nowrap rounded-[4px] border px-[5px] py-[2px] text-[10px] font-semibold ${
          done ? "border-rule text-ink3"
            : wait ? "border-dashed border-rule text-ink3" : "border-signal text-signal"
        }`}>
          {playing ? (lv.status_text || "진행 중") : done ? "종료" : wait ? "배당 대기" : "예정"}
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
              : `${pick.exact ? pick.o.market + " " : "배당 "}${pick.g.bin} — 과거 적중 ${((pick.hit ?? 0) * 100).toFixed(1)}% · 수익률 ${(pick.roi * 100).toFixed(1)}%`}
            className={`whitespace-nowrap rounded px-2 py-[3px] text-[11px] font-semibold ${
              pick.tie
                ? "border border-dashed border-ink3 text-ink3"
                : picked === false
                  ? "border border-sev3 text-sev3"
                  : "bg-ink text-paper"
            }`}
          >
            {pick.tie ? (
              <>고를 근거 없음</>
            ) : (
              <>{pick.o["선택"]} <span className="tnum">{odds(pick.o["배당"])}</span>
                {/* 끝난 경기는 '과거 적중률' 이 아니라 **이 경기에서 맞았나** 를 보여준다 */}
                {picked === true ? <> · 적중 ✔</>
                  : picked === false ? <> · 미적중 ✕</>
                  : pick.hit != null && (
                      <> · 적중 <b className="tnum">{(pick.hit * 100).toFixed(0)}%</b></>)}
                {picked === null && pick.why && <span className="opacity-70"> · {pick.why}</span>}</>
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
        {/* 용지 대조용 게임번호. 화면을 보면서 실제 프로토 용지에 마킹하려면
            이 번호가 있어야 한다 — 없으면 팀 이름으로 용지를 다시 뒤져야 한다.
            프로토는 **마켓 한 줄마다** 번호가 따로 붙으므로 경기가 아니라 옵션 단위다. */}
        <th className={`${th} text-right`}>번호</th>
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
              <td className={`${td} tnum text-right text-ink3 whitespace-nowrap`}>
                {o["게임번호"] || "–"}</td>
              <td className={td}>
                {gr && <GradeBadge grade={t ? "T" : gcls(gr.grade)}
                  title={t ? `양쪽이 같은 배당대(${gr.bin}) — 고를 근거가 없다`
                           : `배당 ${gr.bin} 실측 ${(gr.roi * 100).toFixed(1)}%`} />}
                {o.market}{o.label ? ` ${o.label}` : ""} · {o["선택"]}
                {o["적중"] === true ? " ✔" : o["적중"] === false ? " ✕" : ""}
              </td>
              {/* 실시간으로 갈아끼운 값은 점 하나로 표시한다. 산출 시점 값과
                  구분이 안 되면 "왜 아까랑 다르지" 가 된다. */}
              <td className={`${td} tnum text-right`}>
                {odds(o["배당"])}
                {o._live && <span className="ml-1 text-[10px] text-ink3"
                  title="방금 받아온 값 (5분 주기)">•</span>}
              </td>
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
