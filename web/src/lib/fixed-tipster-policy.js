export const TIPSTER_VOTE_VERSION = "tipster-vote-v1";
export const isTipsterVoteVersion = version => version === TIPSTER_VOTE_VERSION;

// Frozen snapshot metadata takes precedence over the latest game-level intake.
export function tipsterPolicyInfo(game) {
  const raw = game?.decision_snapshot;
  return raw?.tipster_hybrid ?? (isTipsterVoteVersion(raw?.model?.selection_policy)
    || isTipsterVoteVersion(raw?.model?.operating_version) ? game?.tipster_hybrid : null);
}

export function usesTipsterVote(game) {
  const raw = game?.decision_snapshot;
  return isTipsterVoteVersion(raw?.model?.selection_policy)
    || isTipsterVoteVersion(raw?.model?.operating_version)
    || isTipsterVoteVersion(raw?.tipster_hybrid?.version)
    || (!raw && isTipsterVoteVersion(game?.tipster_hybrid?.version));
}

export const isMarketFallback = info => info?.selection_source === "market_fallback" && info?.reason === "market_fallback_no_votes" && info?.status === "applied";

export function tipsterVoteSummary(info) {
  if (isMarketFallback(info)) return { authors: 0, counts: {}, share: null, text: "시장 확률 기준 · 픽스터 없음" };
  const evidence = Array.isArray(info?.evidence) ? info.evidence : [];
  const authors = Number.isInteger(info?.authors) ? info.authors : evidence.length;
  const counts = info?.vote_counts || Object.fromEntries(["홈", "무", "원정"].map(side =>
    [side, evidence.filter(row => row.side === side).length]));
  const side = info?.selected_side;
  const share = authors > 0 && Number.isFinite(counts[side]) ? counts[side] / authors : null;
  return { authors, counts, share, text: `픽스터 투표 · ${authors}명/발행처 · ${Object.entries(counts)
    .filter(([, count]) => count > 0).map(([label, count]) => `${label} ${count}표`).join(" · ") || "유효 투표 없음"}` };
}

export function tipsterWithholdLabel(info) {
  return info?.reason === "tied_votes" || /tie/.test(info?.reason || "")
    ? "픽스터 투표 동률 · 보류" : "픽스터 투표 · 보류";
}
