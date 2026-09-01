import { useEffect, useMemo, useState } from "react";
import { Card, GradeBadge, Nav, OddsChip, Stat } from "../components/ui.jsx";
import PredictionPanel from "../components/PredictionPanel.jsx";
import BetSaveDialog from "../components/BetSaveDialog.jsx";
import BetPreference from "../components/BetPreference.jsx";
import { AiDecisionPath } from "../components/AiDisclosure.jsx";
import { displayCommentary } from "../lib/commentary.js";
import { day, dayTag, formLine, gcls, gradeOf, hhmm, kstMMDD,
  nextKstDateRefreshDelay, odds, pct, sgn } from "../lib/fmt.js";
import { infoTabs, pitcherMetrics, sourceFor, starterFor, teamRecordFor,
  unavailableFor } from "../lib/game-info.js";
import { performanceAnalysis } from "../lib/performance-analysis.js";
import { buildDecisionViewModel } from "../lib/decision-view-model.js";
import { repriceGameOdds } from "../lib/live-odds.js";
import { alignTodayRecommendations, buildTodayMemberships,
  todaySelectionForGame } from "../lib/unified-recommendation.js";
import { usePolledData } from "../lib/poll.js";
import { availableToday, nextTodayRefreshDelay, recommendationFromPlans } from "../lib/today-plan.js";
import { isDataStale, waitingLabel } from "../lib/data-freshness.js";
import { gamePhase, PHASE_LABEL, recommendationOutcome } from "../lib/match-status.js";
import { predictionForGame } from "../lib/game-prediction.js";
import { commentaryMethod, directPickReason } from "../lib/recommendation.js";
import { compactTeamPlayerLine } from "../lib/team-preview.js";
import { deduplicateGameCards } from "../lib/game-dedup.js";

// 실시간 점수만 **수집 머신이 직접 서빙**한다.
// 나머지 산출물(docs/data/*.json)은 git push 로 나르는데 그 주기가 30분이라
// 실시간이 될 수 없다. 3분마다 커밋하면 하루 300커밋이라 레포가 망가지고,
// 브라우저가 네이버 API 를 직접 부르는 건 CORS 로 막힌다. 그래서 이 파일만 별도 경로다.
const LIVE_URL = "https://proto-odds-collector.fly.dev/live_scores.json";

// 배당도 같은 처리다. picks_v2.json 의 배당은 산출물 갱신 때 굳으므로 최대 한 시간
// 낡는다 — 2026-08-13 실측 231건 중 73건(32%)이 원천과 달랐다. 5분마다 갱신되는
// 이 파일로 덮어쓴다.
const ODDS_URL = "https://proto-odds-collector.fly.dev/live_odds.json";
const RECOMMENDATION_URL = "https://proto-odds-collector.fly.dev/today_combo.json";
const PICKS_URL = "https://proto-odds-collector.fly.dev/picks_v2.json";

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
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", load);
    window.addEventListener("online", load);
    return () => {
      stop = true;
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", load);
      window.removeEventListener("online", load);
    };
  }, [url, ms]);
  return data;
}

const useLive = () => usePoll(LIVE_URL, 15000);
const useLiveOdds = () => usePoll(ODDS_URL, 120000);

const SCORE_UNIT_LABEL = {
  bs: "득점",
  sc: "골",
  bk: "득점",
  vl: "세트",
};

// 새 생성기가 한 번 돌기 전의 정적 JSON도 빈칸으로 보이지 않게 한다. 이 fallback은
// 경기 전 λ 평균만 보여주며 상위 스코어 확률은 원장 연동 데이터가 온 뒤 표시한다.
function scoreForecastForView(game) {
  if (game?.score_forecast) return game.score_forecast;
  if (!["경기전", "배당대기"].includes(game?.status) || game?._liveStarted) return null;
  const home = Number(game?.lam_home);
  const away = Number(game?.lam_away);
  if (!Number.isFinite(home) || !Number.isFinite(away)) return null;
  return {
    status: "shadow",
    affects_probability: false,
    expected_scores: {
      home,
      away,
      total: home + away,
      unit: SCORE_UNIT_LABEL[game.sport] || "득점",
    },
    contract: { score_unit_label: SCORE_UNIT_LABEL[game.sport] || "득점" },
    top_scorelines: [],
  };
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

function marketHistoryForGame(game, liveOdds) {
  const rows = Object.values(liveOdds?.markets?.[String(game?.round)] || {}).filter((row) =>
    row?.home === game?.home && row?.away === game?.away && row?.date === game?.date);
  const history = liveOdds?.history?.[String(game?.round)] || {};
  return rows.flatMap((row) => (history[String(row.game_no)] || []).map((entry) => ({
    ...entry, gameNo: String(row.game_no),
  })));
}

export default function Markets() {
  // ⚠️ 예전엔 처음 한 번만 fetch 했다. 수집기는 30분마다 새 JSON 을 올리는데
  //    화면이 첫 로드에 멈춰 있어 새로고침을 눌러야만 바뀌었다. 이제 스스로 갱신한다.
  const { data, at } = usePolledData({
    d: "data/picks_v2.json",
    grades: "data/loss_grades.json",
    today: "data/today_combo.json",
  }, 300000);   // 5분
  const { d: staticPicks, grades, today } = data;
  // 판정도 수집 머신에서 직접 받는다. Git push·Pages 배포를 기다리느라 최신 배당은
  // 보이는데 판정만 3시간 넘게 낡는 상태를 막고, 장애 때는 정적 파일로 복귀한다.
  const livePicks = usePoll(PICKS_URL, 60000);
  const d = livePicks || staticPicks;
  const liveOdds = useLiveOdds();
  const liveToday = usePoll(RECOMMENDATION_URL, 120000);
  const liveFeed = useLive();
  const liveIndex = useMemo(() => buildLiveIndex(liveFeed), [liveFeed]);
  // 실시간 가격 revision을 페이지 최상단에서 한 번만 합친다. 오늘 조합·경기 카드·
  // 배당 비교가 서로 다른 가격 시점을 읽지 않게 같은 객체를 아래로 전달한다.
  const synchronized = useMemo(() => {
    if (!d) return null;
    const merge = (games) => (games || []).map((game) => {
      const repriced = repriceGameOdds(
        game,
        liveOdds?.odds?.[String(game.round)],
        liveOdds?.generated_at || null,
        liveOdds?.markets?.[String(game.round)],
      );
      const marketHistory = marketHistoryForGame(game, liveOdds);
      const withHistory = marketHistory.length ? { ...repriced, _marketHistory: marketHistory } : repriced;
      const liveState = liveOf(liveIndex, withHistory);
      return liveState ? { ...withHistory, _liveState: liveState, _liveStarted: true } : withHistory;
    });
    return { ...d, live: merge(d.live), past: merge(d.past) };
  }, [d, liveOdds, liveIndex]);

  if (at && !synchronized) return <Shell><Empty>데이터를 불러오지 못했습니다</Empty></Shell>;
  if (!synchronized) return <Shell><Empty>불러오는 중…</Empty></Shell>;
  // 경기 원장의 생성 시각이 낡았더라도 현재 회차 배당을 방금 정상 수집했다면 화면
  // 전체를 중단하지 않는다. 각 경기 선택은 repriceGameOdds가 최신 가격으로 다시
  // 판정하며, 식별자가 어긋나는 경우에는 기존 fail-close 규칙이 그대로 막는다.
  const stale = isDataStale(liveOdds?.generated_at || synchronized.generated_at);

  return (
    <Shell meta={metaLine(d, at)}>
      <section id="match-list"><GameList data={synchronized} grades={grades} caps={grades?.odds_caps}
        stale={stale} today={liveToday || today} /></section>
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

const KST_FORMAT = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function kstStamp(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return String(value || "-");
  const parts = Object.fromEntries(
    KST_FORMAT.formatToParts(date).map(({ type, value: part }) => [type, part]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

// `갱신` 은 데이터가 만들어진 시각, `확인` 은 브라우저가 마지막으로 받아 본 시각이다.
// 둘을 나눠 적어야 "화면이 멈춘 건지, 서버가 안 만든 건지"가 구분된다.
const metaLine = (d, at) =>
  // ⚠️ '승부식' 을 못박아 적는다. 수집기가 game_category=pt1 을 하드코딩하고 있어
  //    이 사이트의 모든 숫자는 **승부식 전용**이다. 기록식(pt2)은 한 건도 수집한 적이 없다.
  //    안 적으면 보는 사람이 기록식 배당도 여기 있다고 착각한다.
  `${(d.live || []).length + (d.past || []).length}경기 · 회차 ${(d.rounds || []).join(", ")} · 승부식` +
  ` · 갱신 ${kstStamp(d.generated_at)} KST` +
  (at ? ` · 확인 ${kstStamp(at)} KST` : "");

function Shell({ children, meta }) {
  return (
    <div className="mx-auto max-w-[1180px] px-5 pb-20">
      <Nav current="markets.html" />
      <header className="market-header">
        <div>
          <h1>오늘 경기·배당 분석</h1>
          <p>오늘의 판단을 먼저 보고, 필요한 경기만 열어 흐름·선수·반대 근거를 확인합니다.</p>
        </div>
        {meta && <div className="market-meta">{meta}</div>}
      </header>
      {children}
    </div>
  );
}

const Empty = ({ children }) => (
  <div className="py-7 text-center text-[13px] text-ink3">{children}</div>
);

const planMetric = (plan, current, legacy) => {
  const value = Number(plan?.[current] ?? plan?.[legacy]);
  return Number.isFinite(value) ? value : null;
};

function useTodayClock(today) {
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    let timer;
    const schedule = () => {
      const now = Date.now();
      setClock(now);
      clearTimeout(timer);
      timer = setTimeout(schedule, nextTodayRefreshDelay(today, now));
    };
    const onVisible = () => { if (document.visibilityState === "visible") schedule(); };
    schedule();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", schedule);
    window.addEventListener("pageshow", schedule);
    window.addEventListener("online", schedule);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", schedule);
      window.removeEventListener("pageshow", schedule);
      window.removeEventListener("online", schedule);
    };
  }, [today]);
  return clock;
}

const TodayTab = ({ active, onClick, children }) => (
  <button type="button" aria-pressed={active} onClick={onClick}
    className={`flex items-center gap-1.5 rounded-full border px-[13px] py-1.5 text-[12px] leading-none ${
      active ? "border-ink font-semibold text-ink" : "border-rule text-ink2 hover:border-ink3"
    }`}>
    {children}
  </button>
);

const TodayLeg = ({ pick, highlighted }) => (
  <div className={`today-bet-leg ${highlighted ? "is-highlighted" : ""}`}>
    <span className="tnum text-[11px] text-ink3">{hhmm(pick.date)}</span>
    <span className="rounded border border-rule px-[5px] py-px text-[10px] text-ink3">
      {pick.league}
    </span>
    <span className="min-w-[180px] flex-1 text-[12.5px]">
      {pick.match} — <b>{pick.market}{pick.market_label ? ` ${pick.market_label}` : ""} {pick.sel}</b>
    </span>
    <span className="tnum text-[11px] text-ink3">{pick.round}회 #{pick.game_no}</span>
    <b className="tnum text-[12px]">{odds(pick.odds)}</b>
  </div>
);

function TodayBetRecommendation({ activeToday }) {
  const plans = (activeToday?.plans || []).filter((plan) => plan.ok);
  const derivedRecommendation = recommendationFromPlans(plans);
  const storedRecommendation = activeToday?.recommendation || null;
  const recommendation = storedRecommendation?.action === "buy" ? {
    ...derivedRecommendation,
    ...storedRecommendation,
    target: storedRecommendation.recommended_target ?? storedRecommendation.target
      ?? derivedRecommendation.target,
    why: storedRecommendation.why || derivedRecommendation.why,
  } : derivedRecommendation;
  const solo = activeToday?.solo || null;
  const [selected, setSelected] = useState(0);
  const recommendedPlan = plans.find((plan) => Number(plan.target) === Number(recommendation.target));
  const periodLabel = activeToday?.window === "next_morning" ? "다음 날 오전" : "오늘";
  if (!plans.length && !solo) {
    return <Card className="today-brief today-betting-recommendation">
      <div className="brief-heading"><h2>오늘의 베팅 추천</h2></div>
      <Empty>현재 우리 기준을 통과한 추천 픽이 없습니다. 다음 수집 때 자동으로 다시 판정합니다.</Empty>
    </Card>;
  }
  const recommendedIndex = plans.findIndex(
    (plan) => Number(plan.target) === Number(recommendation.target),
  );
  const selectedIndex = selected < 0 ? -1
    : selected < plans.length ? selected
      : recommendedIndex >= 0 ? recommendedIndex : 0;
  const plan = selectedIndex < 0 ? null : plans[selectedIndex];
  const current = plan || solo;
  const actionLabel = recommendation.action === "buy" ? "오늘의 추천 픽"
    : "추천 없음 · 관찰만";
  const highlighted = recommendation.action !== "pass" && recommendation.action !== "none";
  return (
    <Card className={`today-brief today-betting-recommendation is-${recommendation.action}`}
      aria-label="오늘의 베팅 추천">
      <div className="brief-heading">
        <div><small>{periodLabel} 기준</small><h2>오늘의 베팅 추천</h2></div>
        <p>개편된 판정 원장과 우리 실측 기준으로 선택하며, 추천에 포함된 경기만 아래에서 강조합니다.</p>
      </div>

      <div className="today-bet-verdict">
        <div><small>현재 판정</small><b>{actionLabel}</b></div>
        {recommendedPlan && <>
          <div><small>추천 조합</small><b>{recommendedPlan.legs}폴더 · {recommendedPlan.target}배 목표</b></div>
          <div><small>실배당</small><b className="tnum">{Number(recommendedPlan.actual_odds).toFixed(2)}배</b></div>
          <div><small>예상 적중</small><b className="tnum">{
            planMetric(recommendedPlan, "independent_hit_est", "calibrated_hit_est") != null
              ? `${(planMetric(recommendedPlan, "independent_hit_est", "calibrated_hit_est") * 100).toFixed(1)}%` : "-"
          }</b></div>
        </>}
      </div>
      <p className="mt-2 text-[11px] leading-[1.65] text-ink3">{recommendation.why}</p>

      <div className="my-3 flex flex-wrap gap-1.5" aria-label="추천 조합 선택">
        {solo && <TodayTab active={selectedIndex < 0} onClick={() => setSelected(-1)}>단폴</TodayTab>}
        {plans.map((item, index) => (
          <TodayTab key={item.target} active={selectedIndex === index} onClick={() => setSelected(index)}>
            {item.target}배 · {item.legs}폴
            {Number(item.target) === Number(recommendation.target) && <small>추천</small>}
          </TodayTab>
        ))}
      </div>

      {current && <div className="grid gap-x-7 gap-y-2 border-y border-rule2 py-3 sm:grid-cols-4">
        <Stat k="구성" v={`${plan?.legs || 1}폴더`} />
        <Stat k="실배당" v={`${Number(current.actual_odds || current.odds).toFixed(2)}×`} />
        <Stat k="예상 적중" v={planMetric(current, "independent_hit_est", "calibrated_hit_est") != null
          ? `${(planMetric(current, "independent_hit_est", "calibrated_hit_est") * 100).toFixed(1)}%` : "-"} />
        <Stat k="선택 기준" v={current.selection_basis === "approved_decision_pipeline"
          ? "승인 판정 원장" : "과거 실측 최소손실"} />
      </div>}

      <div className="mt-3">
        {(plan ? plan.picks : [solo]).filter(Boolean).map((pick, index) => (
          <TodayLeg key={`${pick.round}-${pick.game_no}-${index}`} pick={pick} highlighted={highlighted} />
        ))}
      </div>

      <details className="budget-simulator">
        <summary>추천 금액·예산 시뮬레이터</summary>
        <BetPreference plans={plans} solo={solo} selectedIndex={selectedIndex}
          onSelect={setSelected} recommendedTarget={recommendation.target}
          recommendationAction={recommendation.action}
          shouldPass={selectedIndex < 0 || recommendation.action !== "buy"} />
      </details>
    </Card>
  );
}

/* ── 경기별 예측 목록 ─────────────────────────────────────────── */
const STATUS = [
  ["", "전체"], ["live", "진행 중"], ["upcoming", "예정"],
  ["finished", "종료"], ["pending", "결과 확인 중"],
];

function GameList({ data, grades, caps, stale, today }) {
  const [betDraft, setBetDraft] = useState(null);
  // ⚠️ 날짜 기본값은 **오늘**이다. 전체로 두면 목록이 미래 경기로 뒤덮인다 —
  //    2026-08-13 실측: 예정 189건 중 165건(87%)이 아직 배당도 안 나온 8/14 이후
  //    경기였고, 정작 오늘 살 수 있는 6건이 그 속에 묻혔다. 스크롤하면
  //    '배당 대기'만 줄줄이 보여서 "오늘 건데 왜 배당이 없냐"로 읽힌다.
  //    이 페이지의 제목이 '오늘 뭘 사면 덜 잃나' 다. 기본값이 그걸 보여줘야 한다.
  // 날짜는 실제 MM.DD 대신 상대값으로 보관한다. 페이지를 자정 너머 계속 열어
  // 두어도 "오늘"이 어제 날짜에 고정되지 않고 새 KST 날짜를 따라간다.
  const [f, setF] = useState({ st: "", lg: "", mk: "", rd: "", q: "",
                               dt: "today" });
  const [dateClock, setDateClock] = useState(() => Date.now());

  useEffect(() => {
    let timer;
    const refreshDate = () => setDateClock(Date.now());
    const scheduleMidnight = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        refreshDate();
        scheduleMidnight();
      }, nextKstDateRefreshDelay());
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") {
        refreshDate();
        scheduleMidnight();
      }
    };
    scheduleMidnight();
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenVisible);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenVisible);
    };
  }, []);

  // ⚠️ 적중률을 올리는 지렛대는 '뭘 고르나' 가 아니라 **'어느 경기를 버리나'** 다.
  //    실측: 전부 65.9% → 최저배당 ≤1.3 인 경기만 77.6%. ROI 도 같이 좋아진다.
  const [cap, setCap] = useState(0);          // 0 = 제한 없음
  const pool = useMemo(
    () => deduplicateGameCards([...(data.live || []), ...(data.past || [])]),
    [data]);
  const clock = useTodayClock(today);
  const alignedToday = useMemo(() => alignTodayRecommendations(today, pool), [today, pool]);
  const activeToday = useMemo(
    () => stale ? { ...alignedToday, plans: [], solo: null, candidates: [] } : availableToday(alignedToday, clock),
    [alignedToday, clock, stale],
  );
  const todayMemberships = useMemo(() => buildTodayMemberships(activeToday), [activeToday]);
  const selectedDate = f.dt === "today"
    ? kstMMDD(0, dateClock)
    : f.dt === "tomorrow" ? kstMMDD(1, dateClock) : "";

  const uniq = (a) => [...new Set(a)].filter((v) => v != null && v !== "");
  const leagues = useMemo(() => uniq(pool.map((g) => g.league)).sort(), [pool]);
  const markets = useMemo(
    () => uniq(pool.flatMap((g) => (g.options || []).map((o) => o.market))).sort(), [pool]);
  const rounds = useMemo(() => uniq(pool.map((g) => g.round)).sort((a, b) => b - a), [pool]);

  const games = useMemo(() => {
    const q = f.q.trim().toLowerCase();
    return pool.filter((g) =>
      (!f.st || gamePhase(g) === f.st) &&
      (!f.lg || g.league === f.lg) &&
      (!f.rd || String(g.round) === f.rd) &&
      // 날짜 — 회차는 여러 날에 걸쳐 있어서(93회차만 08.07~08.10) 회차 필터로는
      // '오늘 살 수 있는 것'을 못 고른다. 경기일로 직접 거른다.
      (!selectedDate || String(g.date ?? "").slice(0, 5) === selectedDate) &&
      (!q || [g.home, g.away, g.league].join(" ").toLowerCase().includes(q)))
      .sort((a, b) => {
        const order = { live: 0, upcoming: 1, pending: 2, finished: 3 };
        return order[gamePhase(a)] - order[gamePhase(b)]
          || String(a.date).localeCompare(String(b.date));
      });
  }, [pool, f, selectedDate]);

  const phaseCounts = pool.filter((g) => {
    const q = f.q.trim().toLowerCase();
    return (!f.lg || g.league === f.lg) &&
      (!f.rd || String(g.round) === f.rd) &&
      (!selectedDate || String(g.date ?? "").slice(0, 5) === selectedDate) &&
      (!q || [g.home, g.away, g.league].join(" ").toLowerCase().includes(q));
  }).reduce((counts, game) => {
    const phase = gamePhase(game);
    counts[phase] = (counts[phase] || 0) + 1;
    return counts;
  }, {});

  const selectPhase = (phase) => {
    setF((current) => ({ ...current, st: current.st === phase ? "" : phase }));
  };

  const rows = [];
  let cur = null, n = 0;
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
    const todaySelection = wait || stale || g._liveStarted || g._liveOddsChanged
      ? { option: null, membership: null }
      : todaySelectionForGame(todayMemberships, g.options || [], g.round);
    const recommendedTarget = activeToday?.recommendation?.recommended_target
      ?? activeToday?.recommendation?.target;
    const highlightedToday = activeToday?.recommendation?.action === "buy" && (
      activeToday?.recommendation?.action === "solo"
        ? todaySelection.membership?.solo === true
        : todaySelection.membership?.targets?.some(
          (target) => Number(target) === Number(recommendedTarget),
        )
    );
    n++;
    const phase = gamePhase(g);
    const key = `${phase} · ${g.league} · ${day(g.date)}`;
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
          <span className={`match-phase-tag is-${phase}`}>{PHASE_LABEL[phase]}</span>
          <span>{g.league} · {day(g.date)}</span>
        </div>);
    }
    rows.push(<Game key={`${g.league}${g.home}${g.away}${g.date}${n}`} g={g} opts={opts} wait={wait}
      grades={grades} lv={g._liveState || null} stale={stale} generatedAt={data.generated_at}
      year={data.year} todayMembership={todaySelection.membership}
      todayOption={todaySelection.option} highlightedToday={highlightedToday}
      onSaveBet={(game, option) => setBetDraft({ game, option })} />);
  }

    const capRow = cap ? (caps || []).find((c) => c.cap === cap) : null;
  return (
    <>
      <div className="match-section-title">
        <h2>경기 목록</h2>
        <div className="match-phase-counts" aria-label="경기 상태 필터">
          <button type="button" className="is-live" aria-pressed={f.st === "live"} onClick={() => selectPhase("live")}>진행 중 {phaseCounts.live || 0}</button>
          <button type="button" aria-pressed={f.st === "upcoming"} onClick={() => selectPhase("upcoming")}>예정 {phaseCounts.upcoming || 0}</button>
          <button type="button" aria-pressed={f.st === "finished"} onClick={() => selectPhase("finished")}>종료 {phaseCounts.finished || 0}</button>
          <button type="button" aria-pressed={!f.st} onClick={() => selectPhase("")}>전체 {Object.values(phaseCounts).reduce((sum, count) => sum + count, 0)}</button>
        </div>
      </div>
      {stale ? (
        <div className="mb-5 rounded-lg border border-amber-300 bg-amber-50 px-5 py-4 text-amber-950">
          <b className="text-[15px]">데이터 갱신이 지연되고 있습니다</b>
          <p className="mt-1 text-[13px] leading-6">마지막 생성 이후 3시간이 지나 경기 판정을 멈췄습니다. 수집이 복구되면 경기별 픽과 추천 강도가 자동으로 다시 표시됩니다.</p>
        </div>
      ) : <TodayBetRecommendation activeToday={activeToday} />}
      <div className="filter-shell">
        <div className="filter-primary">
          <div className="date-switch" aria-label="경기 날짜">
            <button type="button" aria-pressed={f.dt === "today"} onClick={() => setF({ ...f, dt: "today" })}>오늘</button>
            <button type="button" aria-pressed={f.dt === "tomorrow"} onClick={() => setF({ ...f, dt: "tomorrow" })}>내일</button>
            <button type="button" aria-pressed={!f.dt} onClick={() => setF({ ...f, dt: "" })}>전체</button>
          </div>
          <div className="team-search">
            <input type="search" placeholder="팀 또는 리그 검색" value={f.q}
              onChange={(e) => setF({ ...f, q: e.target.value })} />
          </div>
        </div>
        <details className="advanced-filters">
          <summary><span>상세 조건</span><small>상태 · 리그 · 마켓 · 회차 · 배당 기준</small></summary>
          <div className="filter-grid">
            <label>상태
              <select className="filter-select" value={f.st} onChange={(e) => setF({ ...f, st: e.target.value })}>
                {STATUS.map(([v, l]) => <option key={l} value={v}>{l}</option>)}
              </select>
            </label>
            <Sel label="리그" v={f.lg} opts={leagues} on={(v) => setF({ ...f, lg: v })} cls="filter-select" />
            <Sel label="마켓" v={f.mk} opts={markets} on={(v) => setF({ ...f, mk: v })} cls="filter-select" />
            <Sel label="회차" v={f.rd} opts={rounds} on={(v) => setF({ ...f, rd: v })} cls="filter-select" suffix="회차" />
            <label>최저배당
              <select className="filter-select" value={cap} onChange={(e) => setCap(Number(e.target.value))}>
                <option value={0}>제한 없음</option>
                {(caps || []).filter((c) => c.cap).map((c) => (
                  <option key={c.cap} value={c.cap}>≤{c.cap} · 적중 {(c.hit * 100).toFixed(0)}%</option>
                ))}
              </select>
            </label>

          </div>
          <div className="filter-actions">
            <button type="button" onClick={() => { setF({ st: "", lg: "", mk: "", rd: "", q: "", dt: "today" }); setCap(0); }}>조건 초기화</button>
          </div>
        </details>
      </div>
      {capRow && (
        <p className="mt-2 text-[11.5px] leading-[1.7] text-ink3">
          최저배당 ≤{capRow.cap} 인 경기만 산 과거 실측 —
          적중 <b className="tnum text-ink">{(capRow.hit * 100).toFixed(1)}%</b> ·
          ROI <b className="tnum">{(capRow.roi * 100).toFixed(2)}%</b> ·
          <span className="opacity-70">
            {" "}(전체 경기의 {(capRow.share * 100).toFixed(0)}% · n={capRow.n.toLocaleString()})
          </span>
        </p>
      )}
      <div>
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
      {betDraft && <BetSaveDialog draft={betDraft} onClose={() => setBetDraft(null)} />}
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

function BaseballSituation({ live }) {
  const bases = live?.bases || {};
  const countKnown = [live?.balls, live?.strikes, live?.outs].every((value) => value != null);
  const occupied = [bases.first, bases.second, bases.third].filter((base) => base?.occupied);
  return (
    <div className="live-baseball-situation" aria-label="현재 야구 경기 상황">
      <div className="base-state" aria-label={occupied.length ? `주자 ${occupied.length}명` : "주자 없음"}>
        {[['third', '3루'], ['second', '2루'], ['first', '1루']].map(([key, label]) => (
          <span key={key} className={bases[key]?.occupied ? "is-occupied" : ""}
            title={bases[key]?.runner || `${label} 주자 없음`}>{label}</span>
        ))}
      </div>
      <div><small>현재 타자</small><b>{live?.batter || "확인 중"}</b>
        {countKnown && <span>B {live.balls} · S {live.strikes} · O {live.outs}</span>}</div>
      <div><small>현재 투수</small><b>{live?.pitcher || "확인 중"}</b>
        {live?.next_batter && <span>다음 {live.next_batter}</span>}</div>
    </div>
  );
}

const historyTime = (value) => {
  const stamp = new Date(value);
  return Number.isNaN(stamp.getTime()) ? "시각 미상" : stamp.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
};

function MarketHistory({ rows }) {
  if (!rows?.length) return null;
  const grouped = rows.reduce((result, row) => {
    const key = `${row.gameNo}|${row.market}`;
    if (!result.has(key)) result.set(key, []);
    result.get(key).push(row);
    return result;
  }, new Map());
  const groups = [...grouped.entries()]
    .map(([key, entries]) => ({ key, entries }))
    .filter(({ entries }) => entries.length > 1);
  if (!groups.length) return null;
  return <details className="mb-3 rounded border border-rule2 bg-panel px-3 py-2 text-[12px]">
    <summary className="cursor-pointer font-semibold text-ink">배당·기준점 변경 이력</summary>
    <div className="mt-2 space-y-3">
      {groups.map(({ key, entries }) => <div key={key}>
        <b>{entries[0].market} · 게임번호 {entries[0].gameNo}</b>
        <ol className="mt-1 space-y-1 text-ink2">
          {entries.map((entry, index) => {
            const previous = entries[index - 1];
            const probabilityDelta = entry.probabilities?.map((value, selectionIndex) =>
              previous?.probabilities?.[selectionIndex] == null ? null
                : (Number(value) - Number(previous.probabilities[selectionIndex])) * 100);
            const lineDelta = previous?.line == null || entry.line == null
              ? null : Number(entry.line) - Number(previous.line);
            return <li key={`${entry.observed_at}-${index}`} className="border-t border-rule2 pt-1 first:border-0">
              <span className="tnum">{historyTime(entry.observed_at)}</span>
              {" · "}<strong>{entry.label || "기준점 없음"}</strong>
              {lineDelta ? <em className="ml-1 not-italic text-amber-700">(기준점 {lineDelta > 0 ? "+" : ""}{lineDelta})</em> : null}
              {" · 배당 "}{(entry.odds || []).map((value) => Number(value).toFixed(2)).join(" / ")}
              {entry.probabilities?.length ? <span>
                {" · 예상 적중 "}{entry.probabilities.map((value, selectionIndex) => {
                  const delta = probabilityDelta?.[selectionIndex];
                  return `${Math.round(Number(value) * 1000) / 10}%${delta == null || Math.abs(delta) < .01 ? "" : ` (${delta > 0 ? "+" : ""}${delta.toFixed(1)}%p)`}`;
                }).join(" / ")}
              </span> : null}
            </li>;
          })}
        </ol>
      </div>)}
      <p className="text-[10.5px] leading-5 text-ink3">예상 적중은 해당 시점 배당에서 마진을 제거한 값입니다. 기준점이 바뀌면 판정 대상 자체가 달라지므로 확률 숫자만 단순 비교하지 않습니다.</p>
    </div>
  </details>;
}

function Game({ g, opts, wait, grades, lv, stale, generatedAt, year, todayMembership,
  todayOption, highlightedToday, onSaveBet }) {
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

  // 경기별 선택은 생성기가 확정한 시장 기준 스냅샷만 쓴다. 클라이언트는 별도의
  // 모델 최대확률을 골라 새 추천을 만들지 않는다.
  const done = g.status === "정산";
  const liveClosed = g._liveStarted === true;
  const predictionUnavailable = done || g.prediction_status === "prediction_ledger_required";
  const prediction = wait || stale || predictionUnavailable || liveClosed || g._liveOddsChanged
    ? null : predictionForGame(opts);
  const displayedOption = todayOption || prediction?.option || null;
  const pick = displayedOption ? {
    o: displayedOption,
    g: gradeOf(grades, displayedOption["배당"]),
    tie: false,
  } : null;
  // 프로토 정산은 경기가 끝나고도 한참 뒤다. 그 사이를 실시간 점수가 메운다.
  const playing = !!lv && !lv.finished && !lv.cancelled && !lv.postponed;
  const finished = !!lv?.finished;
  const disruption = lv?.cancelled ? "경기 취소" : lv?.postponed ? "경기 연기" : null;
  const phase = gamePhase(g, lv);
  const outcome = recommendationOutcome(g);
  const waitText = wait ? waitingLabel(g, { generatedAt, year }) : null;
  // 정산 점수가 있으면 그걸 쓰고(확정), 없으면 실시간 점수로 채운다.
  const score = (done && g.score)
    || (lv && lv.home_score != null && lv.away_score != null
        ? [lv.home_score, lv.away_score] : null);
  const analysis = wait || stale || predictionUnavailable || liveClosed
    ? null : performanceAnalysis(g, pick?.o || null, displayCommentary(g));
  const decision = analysis?.decision || buildDecisionViewModel(g, pick?.o || null);
  const forecast = analysis?.prediction;
  const fallbackForecast = disruption || (liveClosed
    ? (finished ? "경기 종료 · 사전 판정 마감" : "경기 시작 · 사전 판정 마감")
    : g._liveOddsChanged
    ? "배당 변경 · 재계산 대기"
    : stale
    ? "최신 데이터 확인 필요"
    : predictionUnavailable
      ? (g.status === "결과확인" ? "정산 결과 확인 필요" : "사전 원장 도입 전 경기")
      : waitText === "상태 확인 불가"
      ? "경기 상태 확인 필요"
      : wait
        ? "배당 발표 전"
        : "분석 자료 확인 중");
  const compactPlayers = compactTeamPlayerLine(analysis?.teamPreviews);
  const pendingLabel = g._liveOddsChanged ? "재계산" : stale ? "중단" : "보류";
  // 시작 전 산출물이 options=[]였던 경기는 해설에도 "배당 미발표"가 박혀 있다.
  // 경기 시작/예정시각 경과 뒤에는 그 문장을 사실처럼 재노출하지 않는다.
  const insightGame = wait && (liveClosed || waitText === "상태 확인 불가")
    ? { ...g, 해설: null, 해설기본: null }
    : g;
  const resultHeadline = outcome.record
    ? `${outcome.record.market}${outcome.record.label ? ` ${outcome.record.label}` : ""} ${outcome.record.selection}`
    : null;
  const targetLabels = todayMembership?.targets?.map((target) => `${target}배`) || [];
  if (todayMembership?.solo) targetLabels.unshift("단폴");
  const todayLabel = targetLabels.length ? `${targetLabels.join(" · ")} 포함` : null;
  return (
    <Card as="details" className={`match-card is-${phase} result-${outcome.state}`}>
      <summary className="match-row">
        <span className="tnum text-[11.5px] text-ink3">{hhmm(g.date)}</span>
        <span className="min-w-0 text-[13.5px] font-semibold">
          <span>
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
          <small className="match-player-inline">
            {disruption || (playing ? `LIVE · ${lv.status_text || "진행 중"}` : done || finished ? `종료 · ${outcome.label}` : wait ? waitText : stale ? "데이터 갱신 지연" : "예정")} · {g.round}회차
            {g._liveLineChanged ? " · 기준점 변경 반영" : ""}
            {compactPlayers
              ? ` · ${compactPlayers}`
              : ""}
          </small>
        </span>
        <span className="match-call-inline">
          <small>{phase === "finished" ? "예측 결과" : playing ? "실시간 경기" : todayLabel ? "오늘 추천 픽" : "경기별 픽"}</small>
          <b>{phase === "finished"
            ? `${outcome.label}${resultHeadline ? ` · ${resultHeadline}` : ""}`
            : pick ? `${pick.o.market}${pick.o.label ? ` ${pick.o.label}` : ""} ${pick.o["선택"]}${todayLabel ? ` · ${todayLabel}` : ""}` : forecast?.headline || fallbackForecast}</b>
        </span>
        <span className="flex gap-1.5">
          {playing ? <span className="live-score-badge"><i />LIVE <b>{lv.status_text || "진행 중"}</b></span>
            : disruption ? <OddsChip label="상태" value={disruption.replace("경기 ", "")} />
            : phase === "finished" ? <span className={`result-badge is-${outcome.state}`}>{outcome.label}</span>
            : liveClosed ? <OddsChip label="판정" value="마감" />
            : wait ? <OddsChip label="배당" value={stale ? "갱신 지연" : waitText === "상태 확인 불가" ? "확인 불가" : "발표 전"} />
            : pick ? <OddsChip
                  label={pick.o["선택"]}
                  value={odds(pick.o["배당"])}
                  grade={pick.g ? gcls(pick.g.grade) : "U"}
                  highlighted={highlightedToday}
                  title={`${pick.o.market}${pick.o.label ? ` ${pick.o.label}` : ""} · ${
                    prediction.recommendation === "recommend" ? "검증 보정이 실제 반영된 추천"
                      : prediction.recommendation === "weak" ? "방향은 제시하되 구매 우위가 약한 픽"
                        : prediction.recommendation === "market" ? "시장 최유력 방향이며 구매 추천은 아님"
                          : "시장 최유력 방향은 제시하되 구매 추천은 관망"
                  } · 배당 기반 시장확률`} />
              : <OddsChip label="판정" value={pendingLabel} />}
        </span>
      </summary>
      <div className="match-detail">
        <MarketHistory rows={g._marketHistory} />
        {g._liveLineChanged && (
          <div className="mb-3 rounded border border-amber-400 bg-amber-50 px-3 py-2 text-[12px] leading-6 text-amber-950" role="status">
            <b>핸디캡·언더오버 기준점 변경 반영</b>
            <p>이전 기준점의 예측과 구조 모델 수치는 폐기했습니다. 현재 기준점과 배당으로 확률 및 추천을 다시 계산했습니다.</p>
          </div>
        )}
        {playing && score && (
          <div className="live-score-panel" role="status" aria-live="polite">
            <div><span>LIVE</span><b>{lv.status_text || "진행 중"}</b></div>
            <strong>{g.home} <em>{score[0]}</em><i>:</i><em>{score[1]}</em> {g.away}</strong>
            <small>새로고침 없이 약 30초마다 자동 갱신됩니다.</small>
            {g.sport === "bs" && <BaseballSituation live={lv} />}
          </div>
        )}
        {phase === "finished" && (
          <div className={`settled-result-panel is-${outcome.state}`}>
            <div>
              <span>최종 결과</span>
              <strong>{g.home} {score ? `${score[0]} : ${score[1]}` : "– : –"} {g.away}</strong>
            </div>
            <div>
              <span>사전 추천 판정</span>
              <strong>{outcome.label}</strong>
              {resultHeadline && <small>{resultHeadline} · 배당 {odds(outcome.record.odds)}</small>}
            </div>
            {outcome.state === "unrecorded" && (
              <p>사전 추천 원장 도입 전에 끝난 경기입니다. 현재 결과를 보고 과거 픽을 새로 만들지 않습니다.</p>
            )}
          </div>
        )}
        {pick && decision.recommendationPriority === "reversal" && (
          <div className="mb-3 rounded border border-dashed border-sev2 bg-panel px-3 py-2 text-[12px] leading-6 text-ink2">
            <b className="text-ink">최종 픽 전환 · {pick.o.market}{pick.o.label ? ` ${pick.o.label}` : ""} {pick.o["선택"]}</b>
            {" · "}<span className="tnum">{odds(pick.o["배당"])}</span>
            {" · 시장 "}<span className="tnum">{pct(pick.o["시장확률"])}</span>
            {" · 구조 모델 차이 "}<span className="tnum">{sgn(Number(pick.o["모델확률"]) - Number(pick.o["시장확률"]))}p</span>
            <p className="text-[10.5px] leading-5 text-ink3">전환 관문을 통과해 기존 정배를 제거하고 이 선택 하나로 교체했습니다. 표시 확률은 해당 배당에서 마진을 제거한 시장확률입니다.</p>
          </div>
        )}
        {wait && (
          <div className="rounded-[2px] border border-dashed border-rule px-2.5 py-2 text-[12px] text-ink3">
            {liveClosed
              ? "실시간 중계에서 경기 시작을 확인해 사전 판정과 구매 후보를 마감했습니다."
              : stale
              ? "오래된 데이터로는 배당 발표 여부를 판단하지 않습니다. 최신 수집이 확인될 때까지 기다려 주세요."
              : waitText === "상태 확인 불가"
                ? "경기 시작 시각이 지났지만 최신 상태를 확인하지 못했다."
                : "배당은 아직 발표되지 않았다. 경기 정보는 먼저 확인할 수 있다."}
          </div>
        )}
        {predictionUnavailable && (
          <div className="rounded-[2px] border border-dashed border-rule px-2.5 py-2 text-[12px] text-ink3">
            경기 전에 저장된 예측 원장이 없어 현재 공식으로 과거 추천을 재구성하지 않습니다.
          </div>
        )}
        <MatchInsight g={insightGame} analysis={analysis}
          decision={predictionUnavailable ? null : decision}
          opts={opts} grades={grades} tie={tie} pick={pick}
          highlightedToday={highlightedToday}
          recalculating={g._liveOddsChanged === true} showPrices={!wait}
          onSaveBet={onSaveBet}
          />
      </div>
    </Card>
  );
}

function OptTable({ g, opts, grades, tie, pick, highlightedToday = false,
  recalculating = false, onSaveBet }) {
  const th = "border-b border-rule2 pb-[5px] pr-2 text-left text-[11px] font-medium text-ink3";
  const td = "border-b border-rule2 py-[5px] pr-2 align-baseline";
  return (
    <table className="w-full border-collapse text-[12.5px]">
      <caption className="sr-only">프로토 배당, 배당 기반 시장확률, 검증 전 구조 AI 수치와 반영 상태</caption>
      <thead><tr>
        {/* 용지 대조용 게임번호. 화면을 보면서 실제 프로토 용지에 마킹하려면
            이 번호가 있어야 한다 — 없으면 팀 이름으로 용지를 다시 뒤져야 한다.
            프로토는 **마켓 한 줄마다** 번호가 따로 붙으므로 경기가 아니라 옵션 단위다. */}
        <th scope="col" className={`${th} text-right`}>번호</th>
        <th scope="col" className={th}>마켓 / 선택</th>
        <th scope="col" className={`${th} text-right`}>배당</th>
        <th scope="col" className={`${th} text-right`}>배당구간 적중</th>
        <th scope="col" className={`${th} model-col text-right`}>배당 기반 확률</th>
        <th scope="col" className={`${th} model-col text-right`}>구조 AI</th>
        <th scope="col" className={`${th} model-col text-right`}>시장과 차이</th>
        <th scope="col" className={th}>판정</th>
        <th scope="col" className={`${th} text-right`}>내 베팅</th>
      </tr></thead>
      <tbody>
        {opts.map((o, k) => {
          const gr = gradeOf(grades, o["배당"]);
          const t = tie[o.market + (o.label || "")];
          return (
            <tr key={k} className={highlightedToday && pick?.o === o ? "is-today-pick" : ""}>
              <td className={`${td} tnum text-right text-ink3 whitespace-nowrap`}>
                {o["게임번호"] || "–"}</td>
              <td className={td}>
                {gr && <GradeBadge grade={t ? "T" : gcls(gr.grade)}
                  title={t ? `양쪽이 같은 배당대(${gr.bin}) — 선택 보류`
                           : `배당 ${gr.bin} 실측 ${(gr.roi * 100).toFixed(1)}%`} />}
                {o.market}{o.label ? ` ${o.label}` : ""} · {o["선택"]}
                {o["적중"] === true ? " ✔" : o["적중"] === false ? " ✕" : ""}
              </td>
              {/* 실시간으로 갈아끼운 값은 점 하나로 표시한다. 산출 시점 값과
                  구분이 안 되면 "왜 아까랑 다르지" 가 된다. */}
              <td className={`${td} tnum text-right`}>
                {odds(o["배당"])}
                {o._live && <span className="ml-1 text-[9.5px] text-ink3"
                  aria-label="실시간 배당으로 확률 재계산 완료">실시간</span>}
              </td>
              <td className={`${td} tnum text-right text-ink3`}>
                {gr?.hit != null ? `${(gr.hit * 100).toFixed(0)}%` : "–"}</td>
              <td className={`${td} model-col tnum text-right`}>{recalculating ? "–" : pct(o["시장확률"])}</td>
              <td className={`${td} model-col tnum text-right`}
                title={o["모델확률"] == null ? "이 종목·마켓은 검증된 구조 모델 확률이 아직 없습니다." : "구조 모델이 산출한 연구 확률"}>
                {recalculating ? "–" : o["모델확률"] == null ? "미산출" : pct(o["모델확률"])}</td>
              <td className={`${td} model-col tnum text-right`}
                title={o["AI잔차"] == null ? "구조 AI 확률이 없어 시장과의 차이를 계산하지 않습니다." : "구조 AI 확률 − 시장확률"}>
                {recalculating ? "–" : o["AI잔차"] == null ? "–" : sgn(o["AI잔차"])}</td>
              <td className={`${td} text-[11.5px]`}>
                {recalculating && <span className="text-ink3">재계산 대기</span>}
                {pick && !pick.tie && pick.o === o && (
                  <span className="font-semibold text-signal">{
                    highlightedToday ? "★ 오늘의 베팅 추천" : "경기 예측 픽"
                  }</span>)}
                {pick && pick.tie && (gradeOf(grades, o["배당"])?.grade === pick.g.grade) && (
                  <span className="text-ink3">동률 — 고를 근거 없음</span>)}
                {!recalculating && (!pick || (!pick.tie && pick.o !== o)) && (
                  <span className="text-ink3">{o["모델확률"] == null ? "시장 참고" : "비추천"}</span>)}
              </td>
              <td className={`${td} text-right`}>
                <button type="button" className="bet-record-button" disabled={recalculating}
                  onClick={() => onSaveBet?.(g, o)}>베팅 기록</button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const PANEL_BTN = "intel-tab";

function SourceStamp({ source }) {
  if (!source) return null;
  let updated = "";
  if (source.updatedAt) {
    const d = new Date(source.updatedAt);
    if (!Number.isNaN(d.getTime())) {
      updated = d.toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      });
    }
  }
  const name = source.url ? (
    <a className="underline decoration-rule underline-offset-2 hover:text-ink"
      href={source.url} target="_blank" rel="noreferrer">{source.name}</a>
  ) : source.name;
  return <div className="source-stamp">출처 {name}{updated ? ` · KST ${updated} 확인` : ""}</div>;
}

function PitcherCard({ team, pitcher }) {
  const metrics = pitcherMetrics(pitcher);
  return (
    <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
      <div className="text-[11px] text-ink3">{team}</div>
      {pitcher ? <>
        <div className="mt-0.5 flex flex-wrap items-baseline gap-1.5">
          <b className="text-[13px] text-ink">{pitcher.name}</b>
          {pitcher.stats?.period && <span className="text-[10.5px] text-ink3">{pitcher.stats.period}</span>}
          {pitcher.stats?.low_sample && <span className="rounded border border-dashed border-rule px-1 text-[9.5px] text-ink3">표본 적음</span>}
        </div>
        {metrics.length ? (
          <div className="mt-2 grid grid-cols-4 gap-x-2 gap-y-1.5 sm:grid-cols-8">
            {metrics.map(([label, value]) => (
              <div key={label}>
                <div className="text-[9.5px] text-ink3">{label}</div>
                <div className="tnum text-[12px] font-semibold text-ink">{value}</div>
              </div>
            ))}
          </div>
        ) : <p className="mt-1 text-[11px] text-ink3">선발 이름은 확인됐지만 개인 지표 자료는 아직 없다.</p>}
        {pitcher.stats?.fip_approx && <div className="mt-1 text-[9.5px] text-ink3">* FIP는 사구 자료가 없어 사구를 제외한 근사치다.</div>}
        {pitcher.stats_source && <div className="mt-1.5 text-[10px] text-ink3">{pitcher.stats_source}</div>}
      </> : <p className="mt-1 text-[11.5px] text-ink3">아직 선발이 발표되지 않았다.</p>}
    </div>
  );
}

function LineupTendency({ team, profile }) {
  if (!profile) return null;
  return (
    <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
      <div className="text-[11px] text-ink3">{team} · 과거 라인업 성향</div>
      <div className="mt-1 text-[11.5px] leading-[1.7]">
        주 포메이션 <b>{profile.formation || "자료 없음"}</b>
        {profile.churn != null && <> · 직전 경기 대비 선발 교체 평균 <b className="tnum">{profile.churn}명</b></>}
        {profile.reserve != null && <> · 비주전 선발 평균 <b className="tnum">{profile.reserve}명</b></>}
      </div>
      <p className="mt-1 text-[10.5px] text-ink3">실제 오늘 선발 명단이 아니라 과거 {profile.n || ""}경기의 팀 성향이다.</p>
    </div>
  );
}

function ActualLineup({ team, players }) {
  if (!players?.length) return null;
  return <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
    <div className="text-[11px] text-ink3">{team} · 발표 라인업</div>
    <ol className="mt-1 grid grid-cols-2 gap-x-2 text-[11px] leading-[1.7]">
      {players.map((p, i) => <li key={`${p.name}-${i}`} className="truncate">
        <span className="tnum mr-1 text-ink3">{p.order || i + 1}</span>
        <b className="font-medium text-ink">{p.name}</b>
        {p.position && <span className="ml-1 text-[9.5px] text-ink3">{p.position}</span>}
        {p.stats && <span className="ml-1 text-[9.5px] text-ink3">
          · {p.stats.apps ?? "-"}경기 {p.stats.goals ?? 0}골 {p.stats.assists ?? 0}도움
        </span>}
      </li>)}
    </ol>
  </div>;
}

function formatBaseballRate(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(3).replace(/^0/, "") : "-";
}

function BaseballLineup({ team, players, referenceDate, lineupState }) {
  if (!players?.length) return null;
  const official = lineupState === "official_today";
  const subtitle = official ? `${referenceDate ? `${referenceDate.replaceAll("-", ".")} · ` : ""}오늘 공식 선발 타순`
    : (referenceDate ? `${referenceDate.replaceAll("-", ".")} 공식 선발 타순 기준 예상` : "예상 라인업");
  return <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
    <div className="text-[11px] text-ink3">{team} · {subtitle}</div>
    <ol className="mt-1 space-y-1.5">
      {players.map((p, i) => {
        const stats = p.stats || null;
        const last = p.last_game || null;
        return <li key={`${p.player_id || p.name}-${i}`} className="text-[11px] leading-[1.45]">
          <div className="flex items-baseline gap-1">
            <span className="tnum w-3 text-ink3">{p.order || i + 1}</span>
            {p.profile_url ? <a href={p.profile_url} target="_blank" rel="noreferrer" className="font-medium text-ink underline decoration-rule2 underline-offset-2 hover:decoration-ink">{p.name}</a> : <b className="font-medium text-ink">{p.name}</b>}
            {p.position && <span className="text-[9.5px] text-ink3">{p.position}</span>}
          </div>
          {stats && <div className="tnum ml-4 text-[9.5px] text-ink3">
            시즌 타율 {formatBaseballRate(stats.avg)} · {stats.home_runs ?? 0}홈런 · {stats.rbi ?? 0}타점 · OPS {formatBaseballRate(stats.ops)}
          </div>}
          {!official && last && <div className="tnum ml-4 text-[9.5px] text-ink3">
            기준 경기 {last.at_bats ?? "-"}타수 {last.hits ?? "-"}안타 · {last.rbi ?? 0}타점
          </div>}
        </li>;
      })}
    </ol>
  </div>;
}

function FootballKeyPlayers({ team, players }) {
  if (!players?.length) return null;
  return <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
    <div className="text-[11px] text-ink3">{team} · 현재 시즌 핵심 선수</div>
    <ul className="mt-1 space-y-1.5">
      {players.slice(0, 5).map((p, i) => <li key={`${p.player_id || p.name}-${i}`} className="text-[11px]">
        <div className="flex items-baseline gap-1">
          <b className="text-ink">{p.name}</b>
          {p.position && <span className="text-[9.5px] text-ink3">{p.position}</span>}
        </div>
        <div className="tnum text-[10px] text-ink3">
          {p.apps != null && <span>{p.apps}경기</span>}
          {p.starts != null && <span> · 선발 {p.starts}</span>}
          {p.goals != null && <span> · {p.goals}골</span>}
          {p.assists != null && <span> · {p.assists}도움</span>}
          {p.xg != null && <span> · xG {p.xg}</span>}
          {p.xa != null && <span> · xA {p.xa}</span>}
        </div>
      </li>)}
    </ul>
    <p className="mt-1.5 text-[9.5px] text-ink3">실제 오늘 선발이 아니라 공식 시즌 기록의 공격 기여 상위 선수다.</p>
  </div>;
}

function CourtRoster({ team, players }) {
  return <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
    <div className="text-[11px] text-ink3">{team} · 등록/최근 대표 명단</div>
    {players?.length ? <ul className="mt-1 grid grid-cols-2 gap-x-2 text-[11px] leading-[1.7]">
      {players.map((p, i) => <li key={`${p.player_id || p.name}-${i}`} className="truncate">
        {p.number != null && p.number !== "" && <span className="tnum mr-1 text-ink3">#{p.number}</span>}
        <b className="font-medium text-ink">{p.name}</b>
        {p.position && <span className="ml-1 text-[9.5px] text-ink3">{p.position}</span>}
        {p.club && <span className="ml-1 text-[9.5px] text-ink3">· {p.club}</span>}
      </li>)}
    </ul> : <p className="mt-1 text-[11px] text-ink3">이 팀의 최종 명단은 아직 공개되지 않았다.</p>}
  </div>;
}

function CourtKeyPlayers({ team, players, sport }) {
  return <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
    <div className="text-[11px] text-ink3">{team} · 공식 기록 상위 선수</div>
    {players?.length ? <ul className="mt-1 space-y-1.5">
      {players.slice(0, 5).map((p, i) => {
        const metrics = sport === "bk"
          ? [["경기", p.games], ["득점", p.points], ["리바운드", p.rebounds], ["도움", p.assists], ["효율", p.efficiency], ["3P%", p.three_pct]]
          : [["순위", p.rank], ["득점", p.points], ["공격", p.attacks], ["블로킹", p.blocks], ["서브", p.serves], ["공격%", p.attack_pct]];
        return <li key={`${p.player_id || p.name}-${i}`} className="text-[11px]">
          <div className="flex items-baseline gap-1">
            <b className="text-ink">{p.name}</b>
            {p.position && <span className="text-[9.5px] text-ink3">{p.position}</span>}
          </div>
          <div className="tnum text-[10px] text-ink3">
            {metrics.filter(([, value]) => value != null).map(([label, value], j) =>
              <span key={label}>{j ? " · " : ""}{label} {value}</span>)}
          </div>
        </li>;
      })}
    </ul> : <p className="mt-1 text-[11px] text-ink3">이 팀의 선수 기록 자료가 아직 연결되지 않았다.</p>}
  </div>;
}

function CourtPlayersPanel({ g }) {
  const info = g["선발"] || {};
  const rosters = info.rosters || {};
  const key = info.key_players || {};
  const hasRoster = !!(rosters.home?.length || rosters.away?.length);
  const hasKey = !!(key.home?.length || key.away?.length);
  return <>
    <div className="mb-2 rounded-[7px] border border-rule2 bg-paper2 px-2.5 py-2 text-[10.5px] leading-[1.6] text-ink3">
      <b className="text-ink">{info.roster_status?.label || info.coverage?.label || "공식 선수 자료원 미연결"}</b>
      {info.roster_status?.label && info.coverage?.label && <span> · {info.coverage.label}</span>}
    </div>
    {hasRoster && <>
      <div className="mb-1 text-[10.5px] font-semibold text-ink3">선수 명단</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <CourtRoster team={g.home} players={rosters.home} />
        <CourtRoster team={g.away} players={rosters.away} />
      </div>
    </>}
    {hasKey && <>
      <div className="mb-1 mt-2 text-[10.5px] font-semibold text-ink3">선수 기록</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <CourtKeyPlayers team={g.home} players={key.home} sport={g.sport} />
        <CourtKeyPlayers team={g.away} players={key.away} sport={g.sport} />
      </div>
    </>}
    {!hasRoster && !hasKey && <p className="text-[11.5px] text-ink3">
      {info.coverage?.label || "이 대회의 공식 선수·명단 자료 경로가 아직 연결되지 않았다."}
    </p>}
    <p className="mt-2 text-[9.5px] leading-[1.6] text-ink3">
      등록 명단과 최근 대표 명단은 실제 당일 선발·출전 확정이나 부상 보고서가 아니다.
    </p>
  </>;
}

function PlayersPanel({ g }) {
  if (g.sport === "bs") {
    const info = g["선발"] || {};
    const lineups = info.lineups || {};
    const status = info.lineup_status || {};
    const sideStates = status.side_states || {};
    const projected = status.official_today !== true;
    const references = status.reference_dates || {};
    return <>
      <div className="grid gap-2 sm:grid-cols-2">
        <PitcherCard team={g.home} pitcher={starterFor(g, "home")} />
        <PitcherCard team={g.away} pitcher={starterFor(g, "away")} />
      </div>
      {(lineups.home?.length || lineups.away?.length) ? <>
        <div className="mb-1 mt-2 text-[10.5px] font-semibold text-ink3">{status.label || (projected ? "최근 공식 경기 기반 예상 타순" : "오늘 공식 선발 타순")}</div>
        <div className="grid gap-2 sm:grid-cols-2">
          <BaseballLineup
            team={g.home} players={lineups.home}
            referenceDate={references.home} lineupState={sideStates.home}
          />
          <BaseballLineup
            team={g.away} players={lineups.away}
            referenceDate={references.away} lineupState={sideStates.away}
          />
        </div>
        {projected && <p className="mt-1.5 text-[9.5px] leading-[1.55] text-ink3">
          {status.caveat || "오늘 실제 발표 라인업이 아니며, 선발 발표 시 교체될 수 있다."}
        </p>}
      </> : <p className="mt-2 text-[10.5px] text-ink3">타자 선발 명단은 아직 발표되지 않았다.</p>}
    </>;
  }
  if (["bk", "vl"].includes(g.sport)) return <CourtPlayersPanel g={g} />;
  const info = g["선발"] || {};
  const actual = info.lineups || {};
  const key = info.key_players || {};
  const formations = info.formations || {};
  const status = info.lineup_status || {};
  const lp = g["라인업"] || {};
  const expected = status.expected_at ? new Date(status.expected_at).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }) : null;
  const hasActual = !!(actual.home?.length || actual.away?.length);
  const hasKey = !!(key.home?.length || key.away?.length);
  const hasTendency = !!(lp.home || lp.away);
  return <>
    {hasActual ? <>
      <div className="mb-1 text-[10.5px] font-semibold text-ink3">실제 발표 선발 명단</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <ActualLineup team={`${g.home}${formations.home ? ` · ${formations.home}` : ""}`} players={actual.home} />
        <ActualLineup team={`${g.away}${formations.away ? ` · ${formations.away}` : ""}`} players={actual.away} />
      </div>
    </> : <p className="text-[11.5px] text-ink3">
      {info.source ? `실제 선발 명단 발표 전${expected ? ` · 통상 ${expected} 전후 확인` : ""}` : "이 리그의 선수·라인업 자료 경로가 아직 연결되지 않았다."}
    </p>}
    {hasKey && <>
      <div className="mb-1 mt-2 text-[10.5px] font-semibold text-ink3">시즌 핵심 선수 기록</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <FootballKeyPlayers team={g.home} players={key.home} />
        <FootballKeyPlayers team={g.away} players={key.away} />
      </div>
    </>}
    {hasTendency && <div className="mt-2 grid gap-2 sm:grid-cols-2">
      <LineupTendency team={g.home} profile={lp.home} />
      <LineupTendency team={g.away} profile={lp.away} />
    </div>}
  </>;
}
function OfficialRecord({ record, sport }) {
  const rank = record.rank ? `${record.rank}위 · ` : "";
  const wl = record.wins != null || record.losses != null
    ? `${record.wins ?? 0}승${record.draws != null ? ` ${record.draws}무` : ""} ${record.losses ?? 0}패`
    : "경기 기록 확인 중";
  return <div className="mt-1.5 border-t border-rule2 pt-1.5 text-[10.5px] leading-[1.65] text-ink3">
    공식 현재 성적 <b className="tnum text-ink">{rank}{wl}</b>
    {sport === "sc" && <>
      {record.goals_per_game != null && <span> · 평균 {record.goals_per_game}득점/{record.conceded_per_game}실점</span>}
      {record.xg != null && <span> · xG {record.xg}{record.xga != null ? `/xGA ${record.xga}` : ""}</span>}
    </>}
    {sport === "bs" && record.pct != null && <span> · 승률 {(record.pct * 100).toFixed(1)}%</span>}
    {sport === "bk" && <>
      {record.points_per_game != null && <span> · 평균 {record.points_per_game}득점{record.conceded_per_game != null ? `/${record.conceded_per_game}실점` : ""}</span>}
      {record.rebounds_per_game != null && <span> · 리바운드 {record.rebounds_per_game}</span>}
      {record.assists_per_game != null && <span> · 도움 {record.assists_per_game}</span>}
      {record.fg_pct != null && <span> · FG {record.fg_pct}%</span>}
      {record.three_pct != null && <span> · 3P {record.three_pct}%</span>}
      {record.turnovers_per_game != null && <span> · 턴오버 {record.turnovers_per_game}</span>}
    </>}
    {sport === "vl" && <>
      {record.table_points != null && <span> · 승점 {record.table_points}</span>}
      {record.set_ratio != null && <span> · 세트비 {record.set_ratio}</span>}
      {record.point_ratio != null && <span> · 점수비 {record.point_ratio}</span>}
      {record.attack_pct != null && <span> · 공격 {record.attack_pct}%</span>}
      {record.blocks_per_set != null && <span> · 블로킹/세트 {record.blocks_per_set}</span>}
      {record.serves_per_set != null && <span> · 서브/세트 {record.serves_per_set}</span>}
      {record.receive_efficiency != null && <span> · 리시브 {record.receive_efficiency}%</span>}
    </>}
  </div>;
}

function TeamFormCard({ team, form, record, sport }) {
  return (
    <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
      <div className="text-[11px] text-ink3">{team}</div>
      {form ? <>
        <div className="mt-0.5 text-[12px] font-semibold text-ink">{form.last10 || formLine(form)}</div>
        <div className="mt-1 text-[11px] leading-[1.7] text-ink2">
          시즌 {form.w}승 {form.d ? `${form.d}무 ` : ""}{form.l}패
          {form.streak ? ` · ${form.streak}` : ""}
          {form.home ? ` · 홈 ${form.home}` : ""}{form.away ? ` · 원정 ${form.away}` : ""}
          {form.avg_scored != null ? ` · 평균 ${form.avg_scored}득점/${form.avg_conceded}실점` : ""}
        </div>
      </> : <p className="mt-1 text-[11.5px] text-ink3">저장된 최근 경기 표본이 부족하다.</p>}
      {record && Object.keys(record).length > 0 && <OfficialRecord record={record} sport={sport} />}
    </div>
  );
}
function TeamsPanel({ g }) {
  const profiles = g["선발"]?.team_profiles || {};
  return <>
    <div className="grid gap-2 sm:grid-cols-2">
      {[['home', g.home, g.form_home], ['away', g.away, g.form_away]].map(([side, team, form]) =>
        <div key={side}>
          <TeamFormCard team={team} form={form} record={teamRecordFor(g, side)} sport={g.sport} />
          {!!profiles[side]?.characteristics?.length && <div className="mt-1 rounded-[7px] border border-rule2 bg-paper2 px-2.5 py-2 text-[10.5px] leading-[1.7] text-ink2">
            <b className="text-ink">{team} 특징</b> · {profiles[side].characteristics.join(" · ")}
          </div>}
        </div>)}
    </div>
    {g["h2h"] && <div className="mt-2 rounded-[7px] border border-rule2 px-2.5 py-2 text-[11.5px] text-ink2">
      <span className="mr-1 text-[10.5px] text-ink3">맞대결</span>{g["h2h"]}
    </div>}
  </>;
}

function AvailabilityTeam({ team, rows, connected, emptyText }) {
  return (
    <div className="rounded-[7px] border border-rule2 px-2.5 py-2">
      <div className="text-[11px] text-ink3">{team}</div>
      {rows.length ? <ul className="mt-1 space-y-1">
        {rows.map((x, i) => <li key={`${x.name}-${i}`} className="text-[11.5px]">
          <b className="text-ink">{x.name}</b>
          <span className="text-ink3"> · {x.reason_label || "사유 미확인"} · {x.status || "출전 불가"}{x.position ? ` · ${x.position}` : ""}</span>
          {x.impact_label && <span className="ml-1 rounded border border-rule px-1 text-[9.5px] text-ink3">예상 영향 {x.impact_label}</span>}
        </li>)}
      </ul> : <p className="mt-1 text-[11.5px] text-ink3">
        {connected ? (emptyText || "공식 명단에서 부상 상태로 표시된 선수가 없다.") : "부상·출전 상태 자료가 아직 연결되지 않았다."}
      </p>}
    </div>
  );
}

function AvailabilityPanel({ g }) {
  const info = g["선발"] || {};
  const un = info.unavailable;
  const state = info.lineup_status?.state;
  const actual = info.lineups || {};
  const hasActual = g.sport === "bs" ? info.lineup_status?.official_today === true : !!(actual.home?.length || actual.away?.length);
  const court = ["bk", "vl"].includes(g.sport);
  const rosterState = info.roster_status?.state;
  const courtConnected = ["official_competition_roster", "official_roster_partial", "official_player_stats", "recent_international_roster", "season_stats"].includes(rosterState);
  const connected = court ? true
    : g.sport === "sc" ? state === "announced"
      : !!un && Object.hasOwn(un, "home") && Object.hasOwn(un, "away");
  const emptyText = court
    ? (courtConnected
      ? "선수 자료는 연결됐지만 별도의 당일 부상·출전 확정 자료는 아니다."
      : (info.coverage?.label || "공식 부상·출전 상태 자료원이 아직 연결되지 않았다."))
    : g.sport === "sc" ? "시즌 핵심 선수들이 모두 선발 명단에 포함됐다." : null;
  const impact = info.availability_summary;
  const burdenLabel = (value) => value >= .6 ? "큼" : value >= .25 ? "중간" : value > 0 ? "작음" : "없음";
  return <>
    {impact && (impact.home_burden > 0 || impact.away_burden > 0) && <div className="mb-2 rounded-[7px] border border-rule2 px-2.5 py-2 text-[11.5px] leading-[1.7] text-ink2">
      명단 변수 예상 영향 · <b>{g.home} {burdenLabel(impact.home_burden)}</b> · <b>{g.away} {burdenLabel(impact.away_burden)}</b>
      <p className="mt-1 text-[10px] text-ink3">선수 비중·결장 가능성·대체 수준·출처 신뢰도를 함께 본 진단값이다. 과거 검증 전이라 승률에는 직접 더하지 않는다.</p>
    </div>}
    <div className="grid gap-2 sm:grid-cols-2">
      <AvailabilityTeam team={g.home} rows={unavailableFor(g, "home")} connected={connected} emptyText={emptyText} />
      <AvailabilityTeam team={g.away} rows={unavailableFor(g, "away")} connected={connected} emptyText={emptyText} />
    </div>
    {!hasActual && <p className="mt-2 text-[10.5px] leading-[1.6] text-ink3">
      {court
        ? "등록/최근 명단은 실제 당일 선발이나 부상 보고서가 아니다. 경기 직전 공식 출전 명단을 별도로 확인한다."
        : g.sport === "sc" ? "실제 선발 명단 발표 전에는 결장·벤치 여부를 단정하지 않는다."
          : "실제 경기 선발 명단은 보통 시작 직전에 확정된다."}
    </p>}
  </>;
}

function ContextEvidence({ g, pick }) {
  const evidence = g["경기근거"] || null;
  const direct = directPickReason(pick);
  const sections = [
    ["경기 내부", evidence?.internal || []],
    ["경기 외부", evidence?.external || []],
    ["공개 픽 흐름", evidence?.crowd || []],
  ].filter(([, rows]) => rows.length);
  const commentary = String(g["근거해설"] || "").trim();
  if (!direct && !sections.length && !commentary && !evidence?.limitations?.length) return null;
  return <div className="mt-3 border-t border-rule2 pt-3 text-[11.5px] leading-[1.7] text-ink2">
    {direct && <div className="rounded-[7px] border border-rule bg-panel px-3 py-2.5">
      <div className="mb-0.5 text-[10.5px] font-semibold tracking-[.04em] text-ink3">왜 이 픽인가</div>
      <div className="text-ink">{direct}</div>
    </div>}
    {!!sections.length && <div className="mt-2 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(210px,1fr))]">
      {sections.map(([title, rows]) => <div key={title} className="rounded-[7px] border border-rule2 px-2.5 py-2">
        <div className="mb-1 text-[10.5px] font-semibold tracking-[.04em] text-ink3">{title}</div>
        {rows.map((row, index) => <div key={`${row.kind || title}-${index}`} className="mb-1 last:mb-0">
          {row.text}{row.source && <span className="ml-1 text-[10px] text-ink3">({row.source})</span>}
        </div>)}
      </div>)}
    </div>}
    {commentary && <div className="mt-2 rounded-[7px] border border-rule2 px-2.5 py-2">
      <span className="mr-1.5 rounded border border-rule px-1.5 py-0.5 text-[10px] text-ink3">
        {commentaryMethod(g["근거해설방식"])}
      </span>
      {commentary}
    </div>}
    {!!evidence?.limitations?.length && <div className="mt-1.5 text-[10.5px] text-ink3">
      검증 메모 · {evidence.limitations.join(" · ")}
    </div>}
  </div>;
}

function MatchInsight({ g, analysis, decision, opts, grades, tie, pick,
  highlightedToday = false, recalculating = false, showPrices = false, onSaveBet }) {
  const txt = displayCommentary(g);
  const tabs = infoTabs(g, txt);
  if (showPrices && !tabs.some((tab) => tab.id === "evidence")) {
    tabs.push({ id: "evidence", label: "픽 근거·수식" });
  }
  const [active, setActive] = useState(tabs[0]?.id || "summary");
  if (!tabs.length) return null;
  const current = tabs.some((x) => x.id === active) ? active : tabs[0]?.id;
  const source = sourceFor(g);
  const uid = String(g.event_id || `${g.round}-${g.home}-${g.away}`).replace(/[^a-zA-Z0-9_-]/g, "-");
  const tabId = (id) => `tab-${uid}-${id}`;
  const panelId = `panel-${uid}`;
  const onTabKeyDown = (event, index) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    setActive(tabs[next].id);
    event.currentTarget.parentElement?.querySelectorAll('[role="tab"]')?.[next]?.focus();
  };
  return (
    <div className="intel-panel">
      <div className="intel-tabs" role="tablist" aria-label={`${g.home} 대 ${g.away} 경기 정보`}>
        {tabs.map((tab, index) => <button key={tab.id} type="button" role="tab"
          id={tabId(tab.id)} aria-controls={panelId}
          aria-selected={current === tab.id}
          tabIndex={current === tab.id ? 0 : -1}
          className={`${PANEL_BTN} ${current === tab.id
            ? "is-active" : ""}`}
          onKeyDown={(event) => onTabKeyDown(event, index)}
          onClick={() => setActive(tab.id)}>{tab.label}</button>)}
      </div>
      <div className="intel-content" role="tabpanel" id={panelId}
        aria-labelledby={tabId(current)} tabIndex={0}>
        {current === "summary" && <>
          {analysis
            ? <PredictionPanel analysis={analysis} scoreForecast={scoreForecastForView(g)} />
            : <p className="m-0 text-[11.5px] text-ink3">{txt || "경기 자료를 확인 중입니다."}</p>}
          <div className="mt-3 border-t border-rule2 pt-3"><TeamsPanel g={g} /></div>
        </>}
        {current === "players" && <>
          <PlayersPanel g={g} />
          <div className="mt-3 border-t border-rule2 pt-3"><AvailabilityPanel g={g} /></div>
        </>}
        {current === "evidence" && <>
          {decision
            ? <AiDecisionPath decision={decision} />
            : <p className="m-0 text-[11.5px] text-ink3">경기 전 판정 스냅샷이 아직 없습니다.</p>}
          <ContextEvidence g={g} pick={pick} />
          {showPrices && <div className="show-model mt-3 overflow-x-auto border-t border-rule2 pt-3">
            <OptTable g={g} opts={opts} grades={grades} tie={tie} pick={pick}
              highlightedToday={highlightedToday}
              recalculating={recalculating} onSaveBet={onSaveBet} />
          </div>}
        </>}
      </div>
      {current === "players" && <SourceStamp source={source} />}
    </div>
  );
}
