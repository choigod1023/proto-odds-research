/** 화면에서 실제로 고른 선택지의 직접 이유를 설명한다.
 *
 * 선수·날씨는 아직 계수 미학습이므로 이 문장에 인과 근거로 넣지 않는다.
 * 경기 컨텍스트는 별도 섹션에서 지지/경고 자료로만 보여 준다.
 */
export function directPickReason(pick) {
  if (!pick) return "";
  if (pick.tie) {
    return `가장 나은 선택지들이 같은 ${pick.g?.bin || "배당"} 구간에 있어 한쪽을 고를 실측 근거가 없다.`;
  }
  const criterion = pick.mode === "roi"
    ? "손실 최소 기준에서 과거 수익률이 가장 덜 나쁜 조합"
    : "적중 우선 기준에서 가장 낮은 배당 구간";
  const cell = pick.exact
    ? `${pick.o.market}·${pick.g.bin}의 과거 실측`
    : `${pick.g.bin} 배당 구간의 과거 실측`;
  const hit = pick.hit == null ? "" : ` 적중률 ${(pick.hit * 100).toFixed(1)}%,`;
  const roi = pick.roi == null ? "" : ` 수익률 ${(pick.roi * 100).toFixed(1)}%`;
  return `${pick.o["선택"]} ${Number(pick.o["배당"]).toFixed(2)}를 표시한 직접 이유는 ${criterion}이기 때문이다. `
    + `${cell}은${hit}${roi}였다. 선수·날씨 정보는 아직 이 선택 점수에 넣지 않았다.`;
}

export function commentaryMethod(status) {
  if (status === "llm_rewritten" || status === "llm_cache") return "LLM 문장 편집";
  if (String(status || "").startsWith("template_")) return "검증 템플릿 · LLM 대체 동작";
  if (status === "disabled_no_key") return "검증 템플릿 · LLM 키 미설정";
  return "경기 해설";
}
