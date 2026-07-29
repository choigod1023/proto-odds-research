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
export function lessBadPick(grades, opts) {
  const scored = (opts || [])
    .map((o) => ({ o, g: gradeOf(grades, o["배당"]) }))
    .filter((x) => x.g);
  if (!scored.length) return null;
  const best = Math.min(...scored.map((x) => RANK[x.g.grade] ?? 9));
  const top = scored.filter((x) => (RANK[x.g.grade] ?? 9) === best);
  // 동률이 여럿이면 배당이 가장 낮은 것 — 같은 등급 안에서도 낮을수록 덜 잃는다
  top.sort((a, b) => a.o["배당"] - b.o["배당"]);
  return { ...top[0], tie: top.length > 1 && top[0].o["배당"] === top[1].o["배당"] };
}
