import { useEffect, useRef, useState } from "react";
import { Stat } from "./ui.jsx";

const STORAGE_KEY = "proto-bet-preference-v1";

const PROFILES = {
  careful: {
    label: "보수형",
    ratio: 0.4,
    target: 2,
    note: "하루 예산의 40%만 투입하고 나머지는 남긴다.",
  },
  balanced: {
    label: "균형형",
    ratio: 0.7,
    target: 3,
    note: "하루 예산의 70%를 목표 3배 안팎 조합에 투입한다.",
  },
  oneshot: {
    label: "원샷형",
    ratio: 1,
    target: 5,
    note: "고른 조합 한 장에 하루 예산을 전부 투입한다. 실패하면 전액 손실이다.",
  },
};

function readPreference() {
  const fallback = { profile: "oneshot", budget: 10_000, target: 5 };
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    const profile = PROFILES[saved?.profile] ? saved.profile : fallback.profile;
    const budget = Number(saved?.budget);
    return {
      profile,
      budget: Number.isFinite(budget) && budget >= 1_000
        ? Math.min(budget, 100_000)
        : fallback.budget,
      target: Number.isFinite(Number(saved?.target)) ? Number(saved.target) : fallback.target,
    };
  } catch {
    return fallback;
  }
}

function nearestPlanIndex(plans, target) {
  if (!plans.length) return -1;
  let best = 0;
  for (let k = 1; k < plans.length; k += 1) {
    if (Math.abs(plans[k].target - target) < Math.abs(plans[best].target - target)) best = k;
  }
  return best;
}

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

export default function BetPreference({ plans, solo, selectedIndex, onSelect }) {
  const [preference, setPreference] = useState(readPreference);
  const initialized = useRef(false);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(preference));
  }, [preference]);

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      const preferred = nearestPlanIndex(plans, preference.target);
      if (preferred >= 0) onSelect(preferred);
      else if (solo) onSelect(-1);
      return;
    }
    if (selectedIndex >= plans.length && plans.length) {
      onSelect(nearestPlanIndex(plans, preference.target));
      return;
    }
    if (selectedIndex >= 0 && plans[selectedIndex]?.target !== preference.target) {
      setPreference((current) => ({ ...current, target: plans[selectedIndex].target }));
    }
  }, [onSelect, plans, preference.target, selectedIndex, solo]);

  const profile = PROFILES[preference.profile];
  const selected = selectedIndex < 0 ? solo : plans[selectedIndex];
  const unit = selectedIndex < 0 ? 1_000 : 100;
  const stake = Math.floor((preference.budget * profile.ratio) / unit) * unit;
  const selectedOdds = selected?.actual_odds || selected?.odds || 0;
  const gross = Math.round(stake * selectedOdds);
  const reserve = preference.budget - stake;

  const chooseProfile = (key) => {
    const next = PROFILES[key];
    setPreference((current) => ({ ...current, profile: key, target: next.target }));
    const index = nearestPlanIndex(plans, next.target);
    if (index >= 0) onSelect(index);
  };

  const changeBudget = (event) => {
    const value = Math.max(1_000, Math.min(100_000, Number(event.target.value) || 1_000));
    setPreference((current) => ({ ...current, budget: value }));
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

      {selected && (
        <>
          <div className="mt-3 grid gap-x-5 gap-y-2 border-y border-rule2 py-3 sm:grid-cols-5">
            <Stat k="실제 투입" v={money(stake)} />
            <Stat k="적중 환급" v={money(gross)} />
            <Stat k="적중 시 순이익" v={`+${money(gross - stake)}`} />
            <Stat k="실패 시 손실" v={`−${money(stake)}`} tone="sev" />
            <Stat k="남겨두는 돈" v={money(reserve)} />
          </div>
          <div className="mt-2 text-[11px] leading-[1.6] text-ink3">
            금액은 성향별 예산 시뮬레이션이다. 적중 환급은 실배당 단순 곱이며 최종 배당과
            구매 가능 여부는 구매 화면에서 다시 확인해야 한다. 설정은 이 브라우저에 저장된다.
          </div>
        </>
      )}
    </>
  );
}
