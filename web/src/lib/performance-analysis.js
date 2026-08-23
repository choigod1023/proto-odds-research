import { playerSummaryFor } from "./player-summary.js";
const number = (value) => Number.isFinite(Number(value)) ? Number(value) : null;

const particle = (name, pair) => {
  const last = String(name || "").trim().at(-1);
  if (!last || !/[가-힣]/.test(last)) return `${name}${pair[1]}`;
  return `${name}${(last.charCodeAt(0) - 0xac00) % 28 ? pair[0] : pair[1]}`;
};

export function predictionFor(game) {
  const options = game?.options || [];
  const main = options.filter((option) => ["승무패", "승패"].includes(option.market)
    && number(option["모델확률"]) !== null);
  const pool = main.length ? main : options.filter((option) => number(option["모델확률"]) !== null);
  const best = [...pool].sort((a, b) => number(b["모델확률"]) - number(a["모델확률"]))[0];
  const outcome = best?.["선택"] || null;
  const draw = ["무", "무승부"].includes(outcome);
  const away = ["패", "원정승", game?.away].includes(outcome);
  const side = draw ? "무승부" : away ? game?.away : game?.home;
  const probability = number(best?.["모델확률"]);
  const marketProbability = number(best?.["시장확률"]);
  return {
    outcome,
    side,
    probability,
    marketProbability,
    margin: probability !== null && marketProbability !== null ? probability - marketProbability : null,
    market: best?.market || null,
    headline: draw ? "팽팽한 흐름 예상" : side ? `${side} 우세` : "예측 자료 확인 중",
  };
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
      `${starters[0].team} ${starters[0].name}와 ${starters[1].team} ${starters[1].name}의 선발 맞대결이다.`);
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
    const sample = unavailable.slice(0, 2).map((row) => `${row.team} ${row.name}`).join(", ");
    playerNotes.push(`${sample}${unavailable.length > 2 ? ` 외 ${unavailable.length - 2}명` : ""}의 출전 여부가 변수다.`);
  }
  return { featuredPlayers, playerNotes };
}

function performanceReasons(game, prediction) {
  const reasons = [];
  for (const [team, form] of [[game?.home, game?.form_home], [game?.away, game?.form_away]]) {
    if (!team || !form) continue;
    if (form.streak && /연승/.test(form.streak)) reasons.push(`${particle(team, ["이", "가"])} ${form.streak} 흐름을 타고 있다.`);
    if (form.trend === "상승") reasons.push(`${particle(team, ["은", "는"])} 최근으로 올수록 득실 내용이 좋아지고 있다.`);
    if (form.trend === "하락") reasons.push(`${particle(team, ["은", "는"])} 최근 경기 내용이 내려가는 흐름이다.`);
  }
  const hs = number(game?.form_home?.avg_scored), as = number(game?.form_away?.avg_scored);
  const hc = number(game?.form_home?.avg_conceded), ac = number(game?.form_away?.avg_conceded);
  if (hs !== null && as !== null && Math.abs(hs - as) >= .7) {
    const team = hs > as ? game.home : game.away;
    reasons.push(`${particle(team, ["이", "가"])} 최근 경기에서 더 꾸준하게 득점 기회를 만들었다.`);
  }
  if (hc !== null && ac !== null && Math.abs(hc - ac) >= .7) {
    const team = hc < ac ? game.home : game.away;
    reasons.push(`${particle(team, ["은", "는"])} 최근 수비에서 상대 득점을 더 잘 억제했다.`);
  }
  const hr = number(game?.["선발"]?.teams?.home?.rank), ar = number(game?.["선발"]?.teams?.away?.rank);
  if (hr !== null && ar !== null && Math.abs(hr - ar) >= 2) {
    const team = hr < ar ? game.home : game.away;
    reasons.push(`${particle(team, ["이", "가"])} 현재 순위와 시즌 흐름에서 앞서 있다.`);
  }
  if (prediction?.side === "무승부") reasons.unshift("두 팀의 최근 흐름 차이가 크지 않아 한쪽이 주도권을 굳히기 어렵다.");
  return [...new Set(reasons)].slice(0, 4);
}

export function performanceAnalysis(game) {
  const prediction = predictionFor(game);
  const reasons = performanceReasons(game, prediction);
  const players = playerSnapshot(game);
  if (!reasons.length) reasons.push("현재 연결된 최근 경기 자료만으로는 뚜렷한 경기력 차이를 단정하기 어렵다.");
  const announced = game?.["선발"]?.lineup_status?.state === "announced"
    || (game?.sport === "bs" && game?.["선발"]?.home);
  return {
    prediction,
    reasons,
    ...players,
    cautions: announced ? [] : ["경기 직전 선발·출전 명단이 바뀌면 예상 흐름도 달라질 수 있다."],
  };
}
