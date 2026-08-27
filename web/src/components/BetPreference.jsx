import { useEffect, useRef, useState } from "react";
import { Stat } from "./ui.jsx";
import { challengeOptions } from "../lib/today-plan.js";

const STORAGE_KEY = "proto-bet-preference-v1";

const PROFILES = {
  careful: {
    label: "보수형",
    ratio: 0.4,
    note: "구매 판정일 때만 하루 예산의 40%를 투입한다.",
  },
  balanced: {
    label: "균형형",
    ratio: 0.7,
    note: "구매 판정일 때만 하루 예산의 70%를 투입한다.",
  },
  oneshot: {
    label: "원샷형",
    ratio: 1,
    note: "구매 판정이면 하루 예산 전부를 투입한다. 실패하면 전액 손실이다.",
  },
};

const CHALLENGE_TARGETS = [
  { target: 3, label: "3배 적중 우선" },
  { target: 5, label: "5배 균형" },
  { target: 8, label: "8배 고위험" },
];

function readPreference() {
  const fallback = { profile: "balanced", budget: 10_000 };
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    const profile = PROFILES[saved?.profile] ? saved.profile : fallback.profile;
    const budget = Number(saved?.budget);
    return {
      profile,
      budget: Number.isFinite(budget) && budget >= 1_000
        ? Math.min(budget, 100_000)
        : fallback.budget,
    };
  } catch {
    return fallback;
  }
}

const planSignature = (plan) => [plan?.target, plan?.actual_odds,
  ...(plan?.picks || []).map((pick) => `${pick.round}-${pick.game_no}`),
].join("|");

const money = (value) => `${Math.round(value).toLocaleString("ko-KR")}원`;

const Choice = ({ active, onClick, children }) => (
  <button
    type="button"
    aria-pressed={active}
    onClick={onClick}
    className={`flex items-center gap-1.5 rounded-full border px-[13px] py-1.5 text-[12px] leading-none ${
      active ? "border-ink font-semibold text-ink" : "border-rule text-ink2 hover:border-ink3"
    }`}
  >
    {children}
  </button>
);

export default function BetPreference({
  plans,
  solo,
  selectedIndex,
  onSelect,
  recommendedTarget,
  recommendationAction,
  shouldPass,
}) {
  const [preference, setPreference] = useState(readPreference);
  const [challenge, setChallenge] = useState(null);
  const [challengeTarget, setChallengeTarget] = useState(3);
  const initialized = useRef(false);
  const appliedRecommendation = useRef(null);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(preference));
  }, [preference]);

  useEffect(() => {
    const recommendationKey = recommendedTarget == null
      ? (solo ? "solo" : "none") : String(recommendedTarget);
    const recommendedIndex = plans.findIndex(
      (plan) => Number(plan.target) === Number(recommendedTarget),
    );
    if (!initialized.current || appliedRecommendation.current !== recommendationKey) {
      initialized.current = true;
      appliedRecommendation.current = recommendationKey;
      if (recommendedIndex >= 0) onSelect(recommendedIndex);
      else if (solo) onSelect(-1);
      return;
    }
    if (selectedIndex >= plans.length && plans.length) {
      onSelect(recommendedIndex >= 0 ? recommendedIndex : 0);
    }
  }, [onSelect, plans, recommendedTarget, selectedIndex, solo]);

  useEffect(() => {
    const target = recommendationAction === "challenge"
      ? Number(recommendedTarget) : 3;
    setChallengeTarget(Number.isFinite(target) ? target : 3);
    setChallenge(null);
  }, [recommendationAction, recommendedTarget]);

  const profile = PROFILES[preference.profile];
  const selected = selectedIndex < 0 ? solo : plans[selectedIndex];
  const challenges = challengeOptions(plans, preference.budget, challengeTarget);
  const automaticChallenge = recommendationAction === "challenge" && challenge == null &&
    Number(challengeTarget) === Number(recommendedTarget) ? challenges[0] : null;
  const effectiveChallenge = challenge === false ? null : (challenge || automaticChallenge);
  const effectiveSignature = effectiveChallenge
    ? planSignature(plans[effectiveChallenge.plan_index]) : null;
  const activeChallenge = shouldPass && effectiveChallenge && selectedIndex >= 0 &&
    planSignature(selected) === effectiveSignature;
  const unit = selectedIndex < 0 ? 1_000 : 100;
  const stake = shouldPass
    ? (activeChallenge ? effectiveChallenge.stake : 0)
    : Math.floor((preference.budget * profile.ratio) / unit) * unit;
  const selectedOdds = selected?.actual_odds || selected?.odds || 0;
  const gross = Math.round(stake * selectedOdds);
  const reserve = preference.budget - stake;

  const chooseProfile = (key) => {
    setPreference((current) => ({ ...current, profile: key }));
  };

  const changeBudget = (event) => {
    const value = Math.max(1_000, Math.min(100_000, Number(event.target.value) || 1_000));
    setChallenge(null);
    setPreference((current) => ({ ...current, budget: value }));
  };

  const chooseChallenge = (option) => {
    const plan = plans[option.plan_index];
    const signature = planSignature(plan);
    if (effectiveChallenge?.stake === option.stake &&
      effectiveSignature === signature && activeChallenge) {
      setChallenge(false);
      return;
    }
    onSelect(option.plan_index);
    setChallenge({ ...option, plan_signature: signature });
  };

  const chooseChallengeTarget = (target) => {
    setChallengeTarget(target);
    const useAutomatic = recommendationAction === "challenge" &&
      Number(target) === Number(recommendedTarget);
    setChallenge(useAutomatic ? null : false);
  };

  return (
    <>
      <div className="mt-2.5 grid gap-3 rounded-lg border border-rule2 bg-paper p-3 sm:grid-cols-[1fr_auto]">
        <div>
          <div className="mb-1.5 text-[11px] text-ink3">나의 투입 성향</div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(PROFILES).map(([key, item]) => (
              <Choice key={key} active={preference.profile === key} onClick={() => chooseProfile(key)}>
                {item.label}
                <span className="tnum text-[10.5px] text-ink3">{item.ratio * 100}%</span>
              </Choice>
            ))}
          </div>
          <div className="mt-1.5 text-[11.5px] leading-[1.55] text-ink2">{profile.note}</div>
        </div>

        <label className="text-[11px] text-ink3">
          하루 예산
          <div className="mt-1 flex items-center gap-1.5">
            <input
              aria-label="하루 예산"
              className="tnum w-[104px] rounded-md border border-rule bg-panel px-2.5 py-1.5 text-right text-[13px] text-ink"
              type="number"
              min="1000"
              max="100000"
              step="1000"
              value={preference.budget}
              onChange={changeBudget}
            />
            <span className="text-[12px] text-ink2">원</span>
          </div>
        </label>
      </div>

      {shouldPass && challenges.length > 0 && (
        <div className="mt-2.5 rounded-lg border border-rule2 bg-panel p-3">
          <div className="text-[12px] font-semibold text-ink">금액별 구매안</div>
          <div className="mt-0.5 text-[10.5px] leading-[1.55] text-ink3">
            {recommendationAction === "challenge"
              ? "현재 우선안은 적중 우선 조합과 예산 10% 수준(최소 1,000원)을 먼저 선택한다. "
              : "목표 배당과 투입 금액을 함께 고른다. 기본은 3배 적중 우선안이다. "}
            시장 우위 신호는 아니며 같은 금액 카드를 다시 누르면 취소된다.
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5" aria-label="목표 배당">
            {CHALLENGE_TARGETS.map((item) => (
              <Choice
                key={item.target}
                active={challengeTarget === item.target}
                onClick={() => chooseChallengeTarget(item.target)}
              >
                {item.label}
              </Choice>
            ))}
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {challenges.map((option) => {
              const plan = plans[option.plan_index];
              const active = effectiveChallenge?.stake === option.stake &&
                effectiveSignature === planSignature(plan) && activeChallenge;
              return (
                <button
                  type="button"
                  key={`${option.stake}-${option.target}`}
                  aria-pressed={active}
                  onClick={() => chooseChallenge(option)}
                  className={`rounded-md border p-2.5 text-left ${
                    active ? "border-ink bg-paper" : "border-rule bg-paper hover:border-ink3"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 text-[11px] text-ink3">
                    <span className="tnum font-semibold text-ink">{money(option.stake)}</span>
                    <span>{option.target}배 목표</span>
                  </div>
                  <div className="mt-1 text-[10.5px] leading-[1.5] text-ink2">
                    실배당 <b className="tnum text-ink">{option.actual_odds.toFixed(2)}×</b><br />
                    시장 추정 적중 <b className="tnum text-ink">{(option.calibrated_hit_est * 100).toFixed(1)}%</b><br />
                    적중 시 <b className="tnum text-ink">+{money(option.net_profit)}</b><br />
                    시장확률 기준 평균손실 <b className="tnum text-sev3">−{money(option.conservative_loss)}</b>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {shouldPass && (
        <div className="mt-2.5 rounded-md border border-sev2 bg-paper px-3 py-2 text-[11.5px] leading-[1.55] text-sev3">
          {selectedIndex < 0
            ? "단폴은 ‘한경기’ 지정 여부를 확인하기 전에는 자동 투입하지 않는다."
            : activeChallenge
              ? `${money(stake)}을 투입하는 고위험 구매안을 선택했다.`
              : recommendationAction === "challenge"
                ? challenge === false && Number(challengeTarget) === Number(recommendedTarget)
                  ? "고위험 구매안의 기본 투입을 해제했다."
                  : `${recommendedTarget}배 1순위에만 기본 금액을 적용한다.`
                : "현재 조합은 구매 기준에 미달했다. 자동 판정 투입액은 0원이다."}
          {" "}이 금액은 자동 구매 신호와 별도로 사용자가 선택한 값이다.
        </div>
      )}

      {selected && (
        <>
          <div className="mt-3 grid gap-x-5 gap-y-2 border-y border-rule2 py-3 sm:grid-cols-5">
            <Stat k={activeChallenge ? "선택 투입" : "판정 투입"} v={money(stake)} />
            <Stat k="적중 환급" v={money(gross)} />
            <Stat k="적중 시 순이익" v={`+${money(gross - stake)}`} />
            <Stat k="실패 시 손실" v={`−${money(stake)}`} tone="sev" />
            <Stat k="남겨두는 돈" v={money(reserve)} />
          </div>
          <div className="mt-2 text-[11px] leading-[1.6] text-ink3">
            조합 선택과 자금 성향은 분리된다. 금액은 예산 시뮬레이션이며 적중 환급은 실배당 단순 곱이다. 최종 배당과
            구매 가능 여부는 구매 화면에서 다시 확인해야 한다. 설정은 이 브라우저에 저장된다.
          </div>
        </>
      )}
    </>
  );
}
