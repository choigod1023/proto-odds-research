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
