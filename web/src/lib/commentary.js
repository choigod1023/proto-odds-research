/**
 * 예전 생성기는 종목 기본값(야구 8.5)을 실제 발매선처럼 해설에 넣었다.
 * 새 생성기가 고쳐져도 이미 배포된 JSON/LLM 캐시에는 그 문장이 남을 수 있으므로,
 * 화면은 options 의 실제 U/O line 과 충돌하는 레거시 문장을 마지막으로 한 번 막는다.
 */
export function displayCommentary(game) {
  const original = String(game?.["해설"] || "").trim();
  if (!original) return "";

  const totals = (game?.options || []).filter((o) => o.market === "언더오버");
  const first = totals.find((o) => Number.isFinite(Number(o.line)));
  const line = first ? Number(first.line) : null;
  if (line == null) return original;

  // 실제 라인이 8.5인 경기는 올바른 문장일 수 있다. 실제 라인이 다른데도 8.5라고
  // 쓴 경우만 옛 하드코딩 버그로 판정한다.
  const staleDefault = line !== 8.5 && /(?:기준선(?:인|은|\(|\s)|기준점(?:인|은|\s))[^.]{0,20}8\.5|8\.5점/.test(original);
  if (!staleDefault) return original;

  const sentences = original
    .replace(/\s*\n+\s*/g, " ")
    .split(/(?<=[.!?])\s+/)
    .filter(Boolean)
    .filter((sentence) => !/8\.5/.test(sentence))
    // 잘못된 기준선에서 파생된 별도 추천 문장도 같이 버린다.
    .filter((sentence) => !/총득점은\s*(?:오버|언더).*(?:추천|예상)/.test(sentence))
    // 평균 기대득점 차를 다득점 차 확률로 오해한 같은 시기 문장도 제거한다.
    .filter((sentence) => !/(?:전력|득점 차|득실 차|팀).*(?:2점\s*차|2점차)\s*이상.*(?:가능|예상|공산)/.test(sentence));

  const under = totals.find((o) => o["선택"] === "언더");
  const over = totals.find((o) => o["선택"] === "오버");
  const candidates = [under, over].filter((o) => Number.isFinite(Number(o?.["모델확률"])));
  const best = candidates.sort((a, b) => Number(b["모델확률"]) - Number(a["모델확률"]))[0];
  const unit = game?.sport === "sc" ? "골" : "점";
  const correction = best
    ? `실제 언더오버 기준점은 ${line}${unit}이며, 득점분포 모델은 ${best["선택"]} ${(Number(best["모델확률"]) * 100).toFixed(1)}%로 본다.`
    : `실제 언더오버 기준점은 ${line}${unit}이다. 기존 8.5${unit} 비교는 잘못된 기본값이라 제외했다.`;

  if (!sentences.length) return correction;
  return [sentences[0], correction, ...sentences.slice(1)].join(" ");
}
