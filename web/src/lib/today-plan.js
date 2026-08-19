const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DATE_TIME = /(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})/;

export function kickoffTime(candidate, year) {
  const iso = Date.parse(candidate?.kickoff_at || "");
  if (Number.isFinite(iso)) return iso;

  const match = String(candidate?.date || "").match(DATE_TIME);
  if (!match) return Number.NaN;
  const [, month, day, hour, minute] = match.map(Number);
  const sourceYear = Number(year) || new Date().getUTCFullYear();
  return Date.UTC(sourceYear, month - 1, day, hour, minute) - KST_OFFSET_MS;
}

const eventKey = (candidate, year) =>
  candidate?.event_key ||
  `${kickoffTime(candidate, year)}|${candidate?.home || ""}|${candidate?.away || candidate?.match || ""}`;

function byNextKickoff(a, b, year) {
  return (
    kickoffTime(a, year) - kickoffTime(b, year) ||
    Number(a.overround || 99) - Number(b.overround || 99) ||
    Number(b.odds || 0) - Number(a.odds || 0)
  );
}

export function pickNextLegs(candidates, bins, year) {
  const used = new Set();
  const picked = new Map();
  const slots = bins
    .map((bin, index) => ({ bin, index }))
    .sort(
      (a, b) =>
        candidates.filter((candidate) => candidate.bin === a.bin).length -
        candidates.filter((candidate) => candidate.bin === b.bin).length,
    );

  for (const slot of slots) {
    const pool = candidates
      .filter(
        (candidate) => candidate.bin === slot.bin && !used.has(eventKey(candidate, year)),
      )
      .sort((a, b) => byNextKickoff(a, b, year));
    if (!pool.length) return null;
    used.add(eventKey(pool[0], year));
    picked.set(slot.index, pool[0]);
  }
  return bins.map((_, index) => picked.get(index));
}

function legacyCandidates(today) {
  const all = [today?.solo, ...(today?.plans || []).flatMap((plan) => plan.picks || [])]
    .filter(Boolean);
  const seen = new Set();
  return all.filter((candidate) => {
    const key = `${eventKey(candidate, today?.year)}|${candidate.market}|${candidate.market_label}|${candidate.sel}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function availableToday(today, now = Date.now()) {
  if (!today) return { plans: [], solo: null, candidates: [], next: null };
  const source = today.candidates?.length ? today.candidates : legacyCandidates(today);
  const candidates = source
    .filter((candidate) => kickoffTime(candidate, today.year) > now)
    .sort((a, b) => byNextKickoff(a, b, today.year));

  const plans = (today.plans || []).map((plan) => {
    const bins = plan.bins?.length ? plan.bins : (plan.picks || []).map((pick) => pick.bin);
    const picks = pickNextLegs(candidates, bins, today.year);
    if (!picks) {
      return { ...plan, ok: false, why: "시작 전인 서로 다른 경기로 조합할 수 없다" };
    }
    return {
      ...plan,
      ok: true,
      bins,
      picks,
      actual_odds: Number(picks.reduce((total, pick) => total * pick.odds, 1).toFixed(2)),
    };
  });

  const solo = candidates
    .filter((candidate) => candidate.bin === "1.0-1.3")
    .sort((a, b) => byNextKickoff(a, b, today.year))[0] || null;

  return {
    ...today,
    plans,
    solo,
    candidates,
    next: candidates[0] || null,
  };
}
