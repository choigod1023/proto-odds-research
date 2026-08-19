import { eligibleAutoSelections } from "./recommendation-policy.js";

// 표시 형식 — 페이지 전체가 같은 규칙을 쓴다.
// 예전엔 배당이 '2' 와 '1.65' 로 섞여 나와 버튼 폭이 흔들렸다.

export const pct = (v) => (v == null ? "–" : (v * 100).toFixed(1) + "%");
export const sgn = (v) =>
  v == null ? "–" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
export const odds = (v) => (v == null ? "–" : Number(v).toFixed(2));

/** '07.29(수) 18:30' → '18:30' */
export const hhmm = (d) => String(d ?? "").slice(-5);
/** '07.29(수) 18:30' → '07.29(수)' */
export const day = (d) => String(d ?? "").slice(0, 8);

/**
 * 오늘(+offset일)의 'MM.DD' — **한국 날짜로** 계산한다.
 *
 * ⚠️ new Date().getMonth() 를 쓰면 보는 사람의 브라우저 표준시를 따른다.
 *    프로토 경기 시각은 전부 KST 이므로, 해외에서 보면 '오늘' 이 하루 어긋나
 *    오늘 살 수 있는 경기가 목록에서 사라진다. 표준시를 못박는다.
 */
export const kstMMDD = (offsetDays = 0) => {
  const t = new Date(Date.now() + offsetDays * 86400000);
  const p = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
  }).formatToParts(t);
  const get = (k) => p.find((x) => x.type === k).value;
  return `${get("month")}.${get("day")}`;
};

/** '08.09(일) 14:00' 이 오늘이면 '오늘', 내일이면 '내일', 아니면 null */
export const dayTag = (d) => {
  const md = String(d ?? "").slice(0, 5);
  if (!md) return null;
  if (md === kstMMDD(0)) return "오늘";
  if (md === kstMMDD(1)) return "내일";
  return null;
};

/**
 * 배당 → 등급 구간. loss_grades.json 의 실측 구간을 그대로 쓴다.
 * bin 은 '1.0-1.3' 또는 '5.0+' 형태다.
 */
export function gradeOf(grades, o) {
  if (!grades || o == null) return null;
  for (const g of grades.odds_bins) {
    const [lo, hi] = g.bin.replace("+", "-999").split("-").map(Number);
    if (o >= lo && o < hi) return g;
  }
  return null;
}

/** '?' 는 CSS 클래스명이 못 되므로 U 로 바꾼다 */
export const gcls = (g) => (g === "?" ? "U" : g);

export function formLine(f) {
  if (!f) return "";
  const b = [`${f.w}승${f.l}패`];
  if (f.streak) b.push(f.streak);
  if (f.last10) b.push(`최근10 ${f.last10}`);
  if (f.avg_scored != null) b.push(`${f.avg_scored}득 ${f.avg_conceded}실`);
  return b.join(" · ");
}

/* 등급 서열. A 가 '좋다' 가 아니라 '덜 나쁘다'. ? 는 판정 보류라 맨 뒤. */
const RANK = { A: 0, B: 1, C: 2, D: 3, "?": 4 };

/**
 * 경기 하나에서 **덜 잃는 선택**을 고른다.
 *
 * ⚠️ 모델 예측으로 고르지 않는다. 모델 기반 추천은 실측 −42.2% 였다
 *    (아무거나 −13.7%). 모델이 시장보다 부정확해서, '기대수익이 큰 곳' 은
 *    곧 '모델이 크게 틀린 곳' 이기 때문이다 — 역선택이다.
 *    대신 이 프로젝트가 네 해 내내 재현한 사실만 쓴다: **낮은 배당대가 덜 잃는다.**
 *
 * 최선 등급이 여럿이면 고를 근거가 없다(tie). 같은 배당대면 실측 ROI 가 같다.
 */
/** (마켓, 배당대) 셀의 실측 — 안정성 관문을 통과한 것만 쓴다. */
export function cellOf(grades, market, bin) {
  const c = (grades?.market_bins || []).find(
    (x) => x.fam === market && x.bin === bin && x.stable);
  return c || null;
}

/**
 * 경기 하나에서 **덜 잃는 선택**을 고른다.
 *
 * ⚠️ 모델 예측으로 고르지 않는다. 모델 기반 추천은 실측 −42.2% 였다
 *    (아무거나 −13.7%). 모델이 시장보다 부정확해서, '기대수익이 큰 곳' 은
 *    곧 '모델이 크게 틀린 곳' 이기 때문이다 — 역선택이다.
 *
 * ⚠️ 배당대만 보면 안 된다. **같은 배당대라도 마켓마다 1~3%p 갈린다** —
 *    1.0–1.3 에서 승무패 −6.29% vs 언더오버 −13.26% (7%p 차이).
 *    그래서 (마켓, 배당대) 실측 셀이 있으면 그 ROI 로 직접 고르고,
 *    없으면 배당대 등급으로 떨어진다.
 */
const BINS = ["1.0-1.3","1.3-1.5","1.5-1.8","1.8-2.2","2.2-3.0","3.0-5.0","5.0+"];

/**
 * 경기 하나에서 살 선택지를 고른다. 기준이 둘이고 **둘은 갈린다.**
 *
 *   mode="hit"  적중 우선 — 가장 낮은 배당대 (동률이면 환급률 → 낮은 배당)
 *   mode="roi"  손실 최소 — (마켓, 배당대) 실측 ROI 가 가장 덜 나쁜 것
 *
 * ⚠️ 두 기준의 실적은 **여기 적지 않는다.** `loss_grades.json` 의 `pick_modes` 가
 *    같은 규칙을 전체 데이터에 돌려 계산한 값이고, 화면은 그걸 읽는다.
 *    예전엔 62.34%/58.80% 를 주석과 <option> 에 손으로 박아뒀는데, 유령 경기를
 *    걷어내고 홀짝 19,012행을 복구하자 둘 다 틀린 값이 됐다.
 *
 * ⚠️ 모델 예측은 쓰지 않는다. 모델 기반 추천은 실측 −42.2% 였다(아무거나 −13.7%) —
 *    모델이 시장보다 부정확해서 '기대수익이 큰 곳' 이 곧 '모델이 크게 틀린 곳' 이다.
 * ⚠️ 배당대만 보면 마켓 차이를 버린다. 같은 1.0–1.3 이라도
 *    승무패 −6.29% vs 언더오버 −13.26% 로 7%p 갈린다. roi 모드가 이걸 쓴다.
 */
export function lessBadPick(grades, opts, mode = "hit") {
  const scored = eligibleAutoSelections(opts)
    .map((o) => {
      const g = gradeOf(grades, o["배당"]);
      if (!g) return null;
      const c = cellOf(grades, o.market, g.bin);
      return { o, g, c, roi: c ? c.roi : g.roi, hit: c ? c.hit : g.hit, exact: !!c };
    })
    .filter(Boolean);
  if (!scored.length) return null;

  const payout = (o) => {
    const n = Number(o.n_way) || 2;
    return n === 2 ? 87.8 : (String(o.market).includes("핸디") ? 86.8 : 87.0);
  };
  const rank = (x) => BINS.indexOf(x.g.bin);

  if (mode === "roi") {
    scored.sort((a, b) => b.roi - a.roi || a.o["배당"] - b.o["배당"]);
  } else {
    // 적중 우선 = 가장 낮은 배당대. 동률이면 환급률 → 낮은 배당.
    scored.sort((a, b) =>
      rank(a) - rank(b) || payout(b.o) - payout(a.o) || a.o["배당"] - b.o["배당"]);
  }

  const t0 = scored[0], t1 = scored[1];
  const same = t1 && (mode === "roi"
    ? Math.abs(t0.roi - t1.roi) < 1e-9
    : rank(t0) === rank(t1) && payout(t0.o) === payout(t1.o));
  const tie = !!same && t0.o["배당"] === t1.o["배당"];
  return {
    ...t0, tie, mode,
    why: tie ? null
      : (t0.exact ? `${t0.o.market} ${t0.g.bin} 실측 ${(t0.roi * 100).toFixed(1)}%`
                  : `배당 ${t0.g.bin}`),
  };
}
