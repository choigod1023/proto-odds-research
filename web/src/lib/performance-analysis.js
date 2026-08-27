import { playerSummaryFor } from "./player-summary.js";
import { buildDecisionViewModel, decisionLabel, resolveDecisionOption } from "./decision-view-model.js";
const number = (value) => {
  if (value === null || value === undefined || (typeof value === "string" && !value.trim())) return null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
};

const particle = (name, pair) => {
  const last = String(name || "").trim().at(-1);
  if (!last || !/[가-힣]/.test(last)) return `${name}${pair[1]}`;
  return `${name}${(last.charCodeAt(0) - 0xac00) % 28 ? pair[0] : pair[1]}`;
};

export function predictionFor(game, recommended = null) {
  const options = game?.options || [];
  const resolved = resolveDecisionOption(game, options);
  // detached 추천 객체나 레거시 game["추천"]은 브라우저에서 이관하지 않는다.
  const best = recommended
    ? (recommended === resolved ? recommended : null)
    : resolved;
  const decision = buildDecisionViewModel(game, best);
  if (decision.action !== "market_reference" || !decision.option) {
    return {
      outcome: null, side: null, probability: null, marketProbability: null,
      shadowProbability: null, margin: 0, market: null, label: "",
      headline: decisionLabel(decision), decision,
    };
  }
  const outcome = best?.["선택"] || null;
  const market = best?.market || null;
  const marketName = String(market || "");
  const totalMarket = marketName.includes("언더오버");
  const handicapMarket = marketName.includes("핸디캡");
  const marginMarket = ["승①패", "승⑤패"].includes(market);
  const firstHalfTeamMarket = ["전반승무패", "전반승패"].includes(market);
  const teamMarket = ["승무패", "승패"].includes(market);
  const draw = /무/.test(String(outcome || ""));
  const away = /원정|패/.test(String(outcome || "")) || outcome === game?.away;
  const outcomeSide = draw ? "무승부" : away ? game?.away : game?.home;
  // 핸디캡·득점·점수차 선택을 실제 승리 방향으로 읽지 않는다.
  const side = teamMarket ? outcomeSide : null;
  const probability = decision.probability.final;
  const marketProbability = decision.probability.market;
  let headline;
  if (totalMarket) headline = `시장 기준 · ${best?.label || "기준점"} ${outcome}`;
  else if (handicapMarket) headline = `시장 기준 · ${best?.label || "핸디캡"} ${outcome}`;
  else if (marginMarket) headline = `시장 기준 · ${market} ${outcome}`;
  else if (firstHalfTeamMarket) {
    headline = draw ? "시장 기준 · 전반 무승부" : `시장 기준 · 전반 ${outcomeSide} 우세`;
  } else if (teamMarket) {
    headline = draw ? "시장 기준 · 팽팽함" : `시장 기준 · ${side} 우세`;
  } else {
    headline = `시장 기준 · ${[market, best?.label, outcome].filter(Boolean).join(" ")}`;
  }
  return {
    outcome,
    side,
    probability,
    marketProbability,
    shadowProbability: decision.probability.aiCandidate,
    margin: decision.probability.aiDeltaApplied,
    market,
    label: best?.label || "",
    headline,
    decision,
  };
}

const winRate = (record) => {
  const match = String(record || "").match(/^(\d+)-(\d+)$/);
  if (!match) return null;
  const games = Number(match[1]) + Number(match[2]);
  return games ? Number(match[1]) / games : null;
};
const recentRate = (form) => {
  const match = String(form?.last10 || "").match(/(\d+)승(?:\s*(\d+)무)?\s*(\d+)패/);
  if (!match) return null;
  const games = Number(match[1]) + Number(match[2] || 0) + Number(match[3]);
  return games ? (Number(match[1]) + Number(match[2] || 0) * .5) / games : null;
};
function directionalSignal(label, homeValue, awayValue, game, threshold = 0) {
  if (homeValue === null || awayValue === null || Math.abs(homeValue - awayValue) <= threshold) return { label, side: null, state: "중립" };
  return { label, side: homeValue > awayValue ? game.home : game.away, state: "우세" };
}
const readableSignal = {
  "최근 10경기": "최근 성적",
  "최근 공수": "최근 득실",
  "홈·원정": "홈·원정 성적",
  "시즌 순위": "시즌 성적",
};
const signalNames = (rows) => rows.map((signal) => readableSignal[signal.label] || signal.label).join("·");

function signalNarrative(prediction, signals, state) {
  const picked = prediction.side;
  const supporting = signals.filter((signal) => signal.side === picked);
  const opposing = signals.filter((signal) => signal.side && signal.side !== picked);
  const opponent = opposing[0]?.side;
  if (state === "일치") {
    return signalNames(supporting) + "에서 모두 " + particle(picked, ["이", "가"]) + " 앞선다. 현재 최종 판정은 AI 보정이 아닌 시장 기준으로 " + picked + " 쪽이다.";
  }
  if (state === "엇갈림") {
    return signalNames(supporting) + "에서는 " + particle(picked, ["이", "가"]) + " 앞서고, " + signalNames(opposing) + "에서는 " + particle(opponent, ["이", "가"]) + " 낫다. 엇갈림은 숨기지 않되 최종 값은 시장 기준으로 유지한다.";
  }
  if (state === "반대") {
    return "확인되는 최근 기록은 " + particle(opponent, ["이", "가"]) + " 앞선다. 시장 기준 방향과 반대이므로 AI 우위로 해석하지 않고 충돌 자료로 남긴다.";
  }
  return "비교할 최근 기록이 충분하지 않아 시장확률만 기준으로 " + picked + " 쪽을 비교 후보로 둔다.";
}
export function signalSummaryFor(game, prediction) {
  if (!prediction?.side || prediction.side === "무승부" || !["승무패", "승패"].includes(prediction.market)) return null;
  const hf = game?.form_home || {}, af = game?.form_away || {};
  const signals = [], hr = recentRate(hf), ar = recentRate(af);
  if (hr !== null || ar !== null) signals.push(directionalSignal("최근 10경기", hr, ar, game, .05));
  const hNet = number(hf.avg_scored) !== null && number(hf.avg_conceded) !== null ? number(hf.avg_scored) - number(hf.avg_conceded) : null;
  const aNet = number(af.avg_scored) !== null && number(af.avg_conceded) !== null ? number(af.avg_scored) - number(af.avg_conceded) : null;
  if (hNet !== null || aNet !== null) signals.push(directionalSignal("최근 공수", hNet, aNet, game, .2));
  const hv = winRate(hf.home), av = winRate(af.away);
  if (hv !== null || av !== null) signals.push(directionalSignal("홈·원정", hv, av, game, .04));
  const homeRank = number(game?.["선발"]?.teams?.home?.rank), awayRank = number(game?.["선발"]?.teams?.away?.rank);
  if (homeRank !== null || awayRank !== null) signals.push(directionalSignal("시즌 순위", homeRank === null ? null : -homeRank, awayRank === null ? null : -awayRank, game));
  const comparable = signals.filter((signal) => signal.side);
  const support = comparable.filter((signal) => signal.side === prediction.side).length;
  const oppose = comparable.filter((signal) => signal.side !== prediction.side).length;
  const state = oppose === 0 ? (support ? "일치" : "자료 부족") : support === 0 ? "반대" : "엇갈림";
  const narrative = signalNarrative(prediction, signals, state);
  return { modelSide: prediction.side, signals, support, oppose, state, narrative };
}
function playerDetail(player, sport) {
  const stats = player?.stats || player || {};
  const bits = [];
  if (sport === "bs") {
    if (number(stats.ops) !== null) bits.push(`OPS ${number(stats.ops).toFixed(3)}`);
    if (number(stats.home_runs) !== null) bits.push(`${stats.home_runs}홈런`);
    if (number(stats.rbi) !== null) bits.push(`${stats.rbi}타점`);
  } else if (sport === "sc") {
    if (number(stats.goals) !== null) bits.push(`${stats.goals}골`);
    if (number(stats.assists) !== null) bits.push(`${stats.assists}도움`);
    if (number(stats.key_passes) !== null) bits.push(`키패스 ${stats.key_passes}`);
  } else if (sport === "bk") {
    if (number(stats.points) !== null) bits.push(`${stats.points}점`);
    if (number(stats.rebounds) !== null) bits.push(`${stats.rebounds}리바운드`);
    if (number(stats.assists) !== null) bits.push(`${stats.assists}도움`);
  } else {
    if (number(stats.points) !== null) bits.push(`${stats.points}득점`);
    if (number(stats.attacks) !== null) bits.push(`공격 ${stats.attacks}`);
    if (number(stats.blocks) !== null) bits.push(`블로킹 ${stats.blocks}`);
  }
  return bits.join(" · ") || player?.position || "주요 선수";
}

function playerScore(player, sport) {
  const stats = player?.stats || player || {};
  if (sport === "bs") return number(stats.ops) ?? (number(stats.home_runs) || 0) / 100;
  if (sport === "sc") return (number(stats.goals) || 0) * 4 + (number(stats.assists) || 0) * 3
    + (number(stats.key_passes) || 0) / 10;
  if (sport === "bk") return number(stats.efficiency) ?? number(stats.points) ?? 0;
  return number(stats.points) ?? 0;
}

const normalizePlayer = (player, team, sport, role) => player?.name ? {
  team,
  name: player.stats?.name || player.name,
  role: role || player.position || "주요 선수",
  detail: playerDetail(player, sport),
} : null;

export function playerSnapshot(game) {
  const info = game?.["선발"] || {};
  const summary = playerSummaryFor(game);
  const featuredPlayers = summary.players.map((player) => ({
    team: player.team,
    name: player.name,
    role: player.position,
    detail: player.role,
    profileUrl: player.profileUrl,
  }));
  const playerNotes = summary.note ? [summary.note] : [];
  if (game?.sport === "bs") {
    const starters = featuredPlayers.filter((player) => player.role === "선발투수");
    if (starters.length === 2) playerNotes.unshift(
      `${starters[0].team} ${particle(starters[0].name, ["과", "와"])} ${starters[1].team} ${starters[1].name}의 선발 맞대결이다.`);
  }

  for (const side of ["home", "away"]) {
    if (featuredPlayers.some((player) => player.team === game?.[side])) continue;
    const keyPlayers = info.key_players?.[side] || [];
    const lineups = info.lineups?.[side] || [];
    const pool = keyPlayers.length ? keyPlayers : lineups;
    const player = [...pool].sort((a, b) => playerScore(b, game?.sport) - playerScore(a, game?.sport))[0];
    const normalized = normalizePlayer(player, game?.[side], game?.sport);
    if (normalized) featuredPlayers.push(normalized);
  }

  const unavailable = ["home", "away"].flatMap((side) =>
    (info.unavailable?.[side] || []).map((row) => ({ ...row, team: game?.[side] })));
  if (unavailable.length) {
    const sample = unavailable.slice(0, 2).map((row) => {
      const detail = [row.reason_label || row.status, row.impact_label && `영향 ${row.impact_label}`].filter(Boolean).join(" · ");
      return `${row.team} ${row.name}${detail ? `(${detail})` : ""}`;
    }).join(", ");
    playerNotes.push(`${sample}${unavailable.length > 2 ? ` 외 ${unavailable.length - 2}명` : ""}의 출전 여부가 변수다.`);
  }
  const availability = info.availability_summary;
  if (availability?.leans) {
    const team = availability.leans === "home" ? game?.away : game?.home;
    playerNotes.push(`확인된 명단 기준으로는 ${team} 쪽 전력 손실 부담이 더 크다. 이 값은 과거 검증 전이라 모델 확률에는 직접 더하지 않았다.`);
  }
  return { featuredPlayers, playerNotes };
}

const metric = (value) => {
  const n = number(value);
  return n === null ? null : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(n);
};

function formSentence(team, form) {
  if (!team || !form) return null;
  const parts = [];
  if (form.last10) parts.push(`최근 10경기 ${form.last10}`);
  else if (number(form.w) !== null || number(form.l) !== null) {
    parts.push(`${number(form.w) || 0}승${number(form.d) ? ` ${number(form.d)}무` : ""} ${number(form.l) || 0}패`);
  }
  if (form.streak) parts.push(`${form.streak} 흐름`);
  if (form.trend === "상승") parts.push("경기 내용도 상승세");
  if (form.trend === "하락") parts.push("다만 최근 경기 내용은 하락세");
  return parts.length ? `${particle(team, ["은", "는"])} ${parts.join(", ")}다` : null;
}

function venueSentence(game) {
  const homeRecord = String(game?.form_home?.home || "");
  const awayRecord = String(game?.form_away?.away || "");
  const readable = (value) => {
    const match = value.match(/^(\d+)-(\d+)$/);
    return match ? `${match[1]}승 ${match[2]}패` : value;
  };
  if (!homeRecord && !awayRecord) return null;
  const parts = [];
  if (homeRecord) parts.push(`${game.home}의 홈 성적은 ${readable(homeRecord)}`);
  if (awayRecord) parts.push(`${game.away}의 원정 성적은 ${readable(awayRecord)}`);
  if (homeRecord && awayRecord) {
    const homeParts = homeRecord.match(/^(\d+)-(\d+)$/);
    const awayParts = awayRecord.match(/^(\d+)-(\d+)$/);
    const homeRate = homeParts ? Number(homeParts[1]) / (Number(homeParts[1]) + Number(homeParts[2])) : null;
    const awayRate = awayParts ? Number(awayParts[1]) / (Number(awayParts[1]) + Number(awayParts[2])) : null;
    const lean = homeRate !== null && awayRate !== null && Math.abs(homeRate - awayRate) >= .08
      ? ` 이 조건에서는 ${homeRate > awayRate ? game.home : game.away} 쪽이 더 안정적이었다.`
      : " 장소에 따른 뚜렷한 우위는 크지 않다.";
    return `${parts.join(", ")}다.${lean}`;
  }
  return `${parts[0]}다. 이번 경기 장소에서의 적응력을 함께 볼 필요가 있다.`;
}

function balanceSentence(game) {
  const hs = number(game?.form_home?.avg_scored), as = number(game?.form_away?.avg_scored);
  const hc = number(game?.form_home?.avg_conceded), ac = number(game?.form_away?.avg_conceded);
  if (hs === null && as === null && hc === null && ac === null) return null;

  const figures = [];
  if (hs !== null || hc !== null) figures.push(
    `${game.home} ${hs !== null ? `${metric(hs)}득점` : ""}${hs !== null && hc !== null ? "·" : ""}${hc !== null ? `${metric(hc)}실점` : ""}`);
  if (as !== null || ac !== null) figures.push(
    `${game.away} ${as !== null ? `${metric(as)}득점` : ""}${as !== null && ac !== null ? "·" : ""}${ac !== null ? `${metric(ac)}실점` : ""}`);

  const reads = [];
  if (hs !== null && as !== null) {
    if (Math.abs(hs - as) < .2) reads.push("득점 생산력은 비슷하다");
    else reads.push(`${hs > as ? game.home : game.away}의 공격 전개가 더 꾸준했다`);
  }
  if (hc !== null && ac !== null) {
    if (Math.abs(hc - ac) < .2) reads.push("실점 억제력도 큰 차이가 없다");
    else reads.push(`${hc < ac ? game.home : game.away}가 수비에서 더 안정적이었다`);
  }
  const interpretation = reads.length ? `${reads.join(", ")}.` : "두 팀을 직접 비교할 수 있는 표본은 아직 충분하지 않다.";
  return `최근 경기당 ${figures.join(", ")}이다. ${interpretation}`;
}

function seasonSentence(game) {
  const home = game?.["선발"]?.teams?.home || {};
  const away = game?.["선발"]?.teams?.away || {};
  const hr = number(home.rank), ar = number(away.rank);
  if (hr === null && ar === null) return null;
  const record = (team) => {
    const bits = [];
    if (number(team.rank) !== null) bits.push(`${team.rank}위`);
    if (number(team.wins) !== null) {
      bits.push(`${team.wins}승${number(team.draws) ? ` ${team.draws}무` : ""}${number(team.losses) !== null ? ` ${team.losses}패` : ""}`);
    }
    if (number(team.points) !== null) bits.push(`${team.points}점`);
    return bits.join(" · ");
  };
  const figures = [record(home) && `${game.home} ${record(home)}`, record(away) && `${game.away} ${record(away)}`].filter(Boolean);
  const read = hr !== null && ar !== null
    ? hr === ar ? "현재 순위상 두 팀의 간격은 없다."
      : `누적 성적과 시즌 안정감은 ${hr < ar ? game.home : game.away} 쪽이 앞선다.`
    : "현재 확인되는 시즌 위치도 경기 흐름을 판단하는 배경이다.";
  return `${figures.join(", ")}. ${read}`;
}

function playerSentence(players) {
  const featured = players?.featuredPlayers || [];
  if (!featured.length && !players?.playerNotes?.length) return null;
  const leads = featured.slice(0, 2).map((player) =>
    `${player.team} ${player.name}(${player.role}${player.detail && player.detail !== player.role ? ` · ${player.detail}` : ""})`);
  const note = players?.playerNotes?.[0];
  const lead = leads.length ? `이번 경기에서 먼저 볼 선수는 ${leads.join(", ")}다. 경기 운영과 승부처에서 이들의 영향력을 확인해야 한다.` : "";
  return `${lead}${note ? ` ${note}` : ""}`.trim();
}

function expectedFlowSentence(game, prediction) {
  const marketName = String(prediction?.market || "");
  if (marketName.includes("언더오버")) {
    const low = String(prediction.outcome || "").includes("언더");
    const firstHalf = marketName.startsWith("전반");
    const homeScored = number(game?.form_home?.avg_scored);
    const awayScored = number(game?.form_away?.avg_scored);
    const total = homeScored !== null && awayScored !== null ? homeScored + awayScored : null;
    const evidence = !firstHalf && total !== null
      ? `두 팀의 최근 경기당 득점 합은 ${metric(total)}점이다.`
      : `${firstHalf ? "전반" : "경기"} 득점 기준의 시장확률을 사용했다.`;
    return `${evidence} ${prediction.label || "발매 기준점"}에서 ${low ? "득점이 크게 벌어지지 않는" : "공격 전개가 이어지는"} 쪽을 시장 기준 비교 후보로 남겼다.`;
  }
  if (marketName.includes("핸디캡")) {
    return `${prediction.label || "발매 핸디캡"} 기준에서 ${prediction.outcome} 쪽을 시장 비교 후보로 둔다. 이는 핸디캡 적용 후 적중 방향이며 실제 승리 예측과는 다르다.`;
  }
  if (["승①패", "승⑤패"].includes(prediction?.market)) {
    return `${prediction.market}의 ${prediction.outcome} 쪽을 시장 비교 후보로 둔다. 이 판정은 승패만이 아니라 발매된 점수 차 조건까지 포함한다.`;
  }
  if (marketName.startsWith("전반")) {
    return `전반 시장에서 ${prediction.outcome} 쪽을 비교 후보로 둔다. 전반 결과와 경기 최종 결과는 별개의 마켓이다.`;
  }
  if (prediction?.side === "무승부") {
    return "양쪽의 강점이 엇갈려 한 팀이 계속 밀어붙이기보다 주도권을 주고받는 접전 가능성을 높게 봤다.";
  }
  const side = prediction?.side;
  if (!side) return "현재 연결된 경기력 자료만으로는 한쪽이 흐름을 계속 가져갈 것이라고 단정하기 어렵다.";
  const isHome = side === game?.home;
  const ownForm = isHome ? game?.form_home : game?.form_away;
  const ownRecord = isHome ? ownForm?.home : ownForm?.away;
  const bases = [];
  if (ownForm?.streak && /연승/.test(ownForm.streak)) bases.push(ownForm.streak);
  if (ownForm?.trend === "상승") bases.push("최근 상승 흐름");
  if (ownRecord) bases.push(isHome ? "홈 경기 경쟁력" : "원정 경기 경쟁력");
  const basis = bases.length ? bases.slice(0, 2).join("과 ") : "현재 확인된 경기 정보";
  const opponent = isHome ? game?.away : game?.home;
  const ownConceded = number(ownForm?.avg_conceded);
  const otherScored = number((isHome ? game?.form_away : game?.form_home)?.avg_scored);
  const caveat = ownConceded !== null && otherScored !== null && otherScored > ownConceded
    ? ` 다만 ${opponent}의 최근 득점 생산력은 경기를 쉽게 벌리지 못하게 할 변수다.`
    : " 다만 경기 초반 실점 여부와 선발 구성은 흐름을 바꿀 수 있다.";
  return `${particle(side, ["이", "가"])} ${particle(basis, ["을", "를"])} 바탕으로 주도권을 조금 더 오래 가져갈 가능성을 높게 봤다.${caveat}`;
}

function performanceReasons(game, prediction, players) {
  const reasons = [];
  const recent = [formSentence(game?.home, game?.form_home), formSentence(game?.away, game?.form_away)].filter(Boolean);
  if (recent.length) reasons.push(`최근 분위기 — ${recent.join(". ")}.`);
  const balance = balanceSentence(game);
  if (balance) reasons.push(`공격·수비 균형 — ${balance}`);
  const venue = venueSentence(game);
  if (venue) reasons.push(`홈·원정 조건 — ${venue}`);
  const season = seasonSentence(game);
  if (season) reasons.push(`시즌 위치 — ${season}`);
  const player = playerSentence(players);
  if (player) reasons.push(`선수 변수 — ${player}`);
  reasons.push(`예상 경기 흐름 — ${expectedFlowSentence(game, prediction)}`);
  return [...new Set(reasons)].slice(0, 6);
}

export function performanceAnalysis(game, recommended = null, commentary = "") {
  const prediction = predictionFor(game, recommended);
  const signalSummary = signalSummaryFor(game, prediction);
  const players = playerSnapshot(game);
  const reasons = performanceReasons(game, prediction, players);
  const announced = game?.["선발"]?.lineup_status?.state === "announced"
    || (game?.sport === "bs" && game?.["선발"]?.home);
  const opposingSignals = (signalSummary?.signals || [])
    .filter((signal) => signal.side && signal.side !== prediction.side)
    .map((signal) => `${readableSignal[signal.label] || signal.label}은 ${signal.side} 쪽이 앞선다.`);
  const cautions = [
    ...opposingSignals,
    ...(announced ? [] : ["경기 직전 선발·출전 명단이 바뀌면 예상 흐름도 달라질 수 있다."]),
  ];
  return {
    prediction,
    decision: prediction.decision,
    signalSummary,
    reasons,
    commentary: String(commentary || "").trim(),
    ...players,
    cautions: [...new Set(cautions)],
  };
}
