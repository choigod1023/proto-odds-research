import { useState } from "react";
import { Card, Nav, SectionTitle, ThemeToggle } from "../components/ui.jsx";
import { usePolledData } from "../lib/poll.js";

const signed = (value, digits = 5) => value == null ? "—" : `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(digits)}`;
const pvalue = (value) => value == null ? "—" : Number(value).toFixed(3);

export default function Lotto() {
  const { data, at } = usePolledData({ lotto: "data/lotto_latest.json" }, 300000);
  const d = data.lotto;
  const [tickets, setTickets] = useState(10);
  const [copied, setCopied] = useState(false);

  if (at && !d) return <Shell><p className="py-8 text-sev3">로또 검증 데이터를 불러오지 못했습니다.</p></Shell>;
  if (!d) return <Shell><p className="py-8 text-ink3">검증 결과를 불러오는 중…</p></Shell>;

  const p = d.portfolio;
  const audit = d.audit;
  const backtest = d.backtest;
  const maxTickets = p.combinations?.length || 0;
  const shown = Math.max(1, Math.min(tickets, maxTickets));
  const combos = (p.combinations || []).slice(0, shown);
  const options = [...new Set([1, 3, 5, 10, 20, 30, 50, 100, maxTickets])]
    .filter((n) => n > 0 && n <= maxTickets).sort((a, b) => a - b);
  const status = p.model_status === "validated_weighting" ? "검증 가중치 사용" : "균등 복귀";

  const copy = async () => {
    const text = combos.map((row, i) => `${String.fromCharCode(65 + i)}. ${row.numbers.join(" ")}`).join("\n");
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Shell>
      <header>
        <h1 className="mt-[22px] mb-1 text-[22px] leading-snug tracking-[-.01em]">로또 6/45 검증실</h1>
        <p className="m-0 text-[13.5px] text-ink2">패턴을 믿기 전에 균등모델과 미공개 회차에서 먼저 겨룬다.</p>
        <div className="mt-1.5 text-[11.5px] text-ink3">
          공식 {d.draw_count.toLocaleString("ko-KR")}회 · 데이터 마감 {d.data_cutoff_draw_no}회 · {d.data_cutoff_draw_date}
        </div>
      </header>

      <Card className={`mt-4 border-l-[3px] ${p.model_status === "validated_weighting" ? "border-l-sev2" : "border-l-sev0"} px-4 py-4`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="tick">{p.target_draw_no}회 운영 판정</div>
            <div className="mt-0.5 text-[20px] font-semibold">{status}</div>
          </div>
          <div className="tnum rounded-md border border-rule px-3 py-1.5 text-[12px] text-ink2">
            edge weight {Number(p.operational_edge_weight).toFixed(1)}
          </div>
        </div>
        <p className="mt-2.5 mb-0 text-[13px] leading-[1.75] text-ink2">
          {p.model_status === "validated_weighting"
            ? "베이지안 축소 모델이 Brier score와 조합 로그점수 관문을 모두 통과해 번호 가중치를 사용한다."
            : "번호 빈도와 미래상수 후보가 워크포워드에서 균등 기준을 이기지 못했다. 번호별 가중치는 정확히 같게 되돌리고, 고유 조합·인기 패턴 회피·조합 간 중첩만 최적화했다."}
        </p>
      </Card>

      <div className="mt-3 grid gap-2.5 sm:grid-cols-4">
        <Metric label="무작위성 감사" value={audit.uniform_compatible ? "균등과 구별 안 됨" : "탐색 신호"} />
        <Metric label="워크포워드 선택" value={modelLabel(backtest.chosen_model)} />
        <Metric label="지속상태 ρ" value={Number(p.rho_after_noise_shrinkage).toFixed(4)} />
        <Metric label="1등 확률 · 공정 가정" value={`1 / ${Math.round(1 / (shown / 8145060)).toLocaleString("ko-KR")}`} />
      </div>

      <SectionTitle note="1장 1,000원 · 실제 구매 기능 없음">예산 안에서 볼 조합</SectionTitle>
      <Card className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-3">
        <label className="text-[13px] text-ink2">
          표시 예산&nbsp;
          <select className="rounded-md border border-rule bg-panel px-2 py-1 text-ink" value={shown}
            onChange={(event) => setTickets(Number(event.target.value))}>
            {options.map((n) => <option key={n} value={n}>{(n * 1000).toLocaleString("ko-KR")}원 · {n}장</option>)}
          </select>
        </label>
        <button onClick={copy} className="rounded-md border border-rule bg-panel px-3 py-1 text-[12px] text-ink2 hover:text-ink">
          {copied ? "복사됨" : "번호 복사"}
        </button>
      </Card>
      <div className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
        {combos.map((row, index) => <Combination key={row.numbers.join("-")} row={row} index={index} />)}
      </div>
      <p className="mt-2 text-[11.5px] leading-[1.7] text-ink3">
        고유 {shown}조합의 공정 추첨 1등 확률은 {shown.toLocaleString("ko-KR")} / 8,145,060이다.
        조합 간 거리는 하위등수 결과의 동조를 줄이지만 1등 확률을 추가로 높이지 않는다.
      </p>

      <SectionTitle note={`${audit.simulations}개 전체 원장 모의실험`}>균등 비복원 추첨 감사</SectionTitle>
      <Card className="px-4 py-3.5">
        <p className="mt-0 mb-3 text-[13px] leading-[1.75] text-ink2">45개 번호와 990개 번호쌍을 훑은 뒤 가장 튀는 값만 고르는 효과까지 모의실험으로 보정했다. p값이 작을수록 균등 추첨에서 보기 드문 결과다.</p>
        <div className="grid gap-3 sm:grid-cols-3">
          <AuditCell label="번호 빈도 최대편차" stat={audit.observed.max_abs_frequency_z} p={audit.familywise_monte_carlo_p.max_abs_frequency_z} />
          <AuditCell label="번호쌍 최대편차" stat={audit.observed.max_abs_pair_z} p={audit.familywise_monte_carlo_p.max_abs_pair_z} />
          <AuditCell label="회차간 자기상관" stat={audit.observed.max_abs_lag1} p={audit.familywise_monte_carlo_p.max_abs_lag1} />
        </div>
      </Card>

      <SectionTitle note={`${backtest.evaluation_draw_range.join("–")}회 완전 미공개 평가`}>모델이 실제로 균등을 이겼나</SectionTitle>
      <Card className="overflow-x-auto px-4 py-3.5">
        <table className="w-full min-w-[520px] border-collapse text-[12.5px]">
          <thead><tr>{["후보", "Brier 개선", "조합 로그점수 개선", "후반 재현", "판정"].map((h) =>
            <th key={h} className="border-b border-rule2 pb-1.5 pr-3 text-left text-[11px] font-medium text-ink3">{h}</th>)}</tr></thead>
          <tbody>{Object.entries(backtest.models).map(([name, row]) => (
            <tr key={name}>
              <td className="border-b border-rule2 py-2 pr-3 font-medium">{modelLabel(name)}</td>
              <td className={`tnum border-b border-rule2 py-2 pr-3 ${row.brier_gain.mean < 0 ? "text-sev3" : ""}`}>{signed(row.brier_gain.mean)}</td>
              <td className={`tnum border-b border-rule2 py-2 pr-3 ${row.joint_log_gain.mean < 0 ? "text-sev3" : ""}`}>{signed(row.joint_log_gain.mean, 4)}</td>
              <td className="tnum border-b border-rule2 py-2 pr-3">{signed(row.recent_half_log_gain, 4)}</td>
              <td className={`border-b border-rule2 py-2 font-semibold ${row.gate_passed ? "text-sev2" : "text-ink3"}`}>{row.gate_passed ? "통과" : "탈락"}</td>
            </tr>
          ))}</tbody>
        </table>
        <p className="mb-0 mt-2.5 text-[11.5px] leading-[1.7] text-ink3">양수면 균등보다 낫다. 두 지표의 95% 신뢰하한과 평가 후반부가 모두 양수일 때만 번호 가중치를 켠다.</p>
      </Card>

      <SectionTitle>미래상수는 어디에 썼나</SectionTitle>
      <Card className="px-4 py-3.5">
        <div className="grid gap-3 sm:grid-cols-3">
          <FutureCell symbol="C" title="지속 가능 상태" text={`pooled ρ ${Number(p.rho_raw).toFixed(4)} → 잡음 축소 후 ${Number(p.rho_after_noise_shrinkage).toFixed(4)}`} />
          <FutureCell symbol="R" title="다음 회차 순수 충격" text="평균 0. 예측 점수에는 넣지 않고 민감도 시뮬레이션에만 사용" />
          <FutureCell symbol="U" title="미관측 장비·환경" text="공식 확인값이 없으면 임의 보너스 금지. 불확실성으로만 유지" />
        </div>
      </Card>

      <SectionTitle>사전등록</SectionTitle>
      <Card className="px-4 py-3.5 text-[12.5px] leading-[1.8]">
        <KeyValue k="대상 / 데이터 마감" v={`${d.preregistration.target_draw_no}회 / ${d.preregistration.data_cutoff_draw_no}회`} />
        <KeyValue k="모델 버전" v={d.preregistration.model_version} />
        <KeyValue k="생성 시각" v={d.preregistration.generated_at} />
        <KeyValue k="데이터 SHA-256" v={d.preregistration.data_hash} mono />
        <KeyValue k="예측 SHA-256" v={d.preregistration.prediction_hash} mono />
        <p className="mb-0 mt-2 text-[11.5px] text-ink3">결과가 나온 뒤 시드나 실패 조합을 바꾸지 않는다. 같은 대상 회차의 기존 등록은 덮어쓰지 않는다.</p>
      </Card>

      <details className="card mt-3 rounded-card border border-rule bg-panel">
        <summary className="cursor-pointer px-4 py-3 text-[13px] font-semibold">확률 수식과 해석 보기</summary>
        <div className="px-4 py-3 text-[12.5px] leading-[1.85] text-ink2">
          <p className="mt-0"><b className="text-ink">균등 포함확률</b> P(i 포함)=6/45. <b className="text-ink">고유 조합</b> P(S)=1/C(45,6)=1/8,145,060.</p>
          <p><b className="text-ink">가중 부분집합</b> P(S|η)=exp(Σᵢ∈S ηᵢ) / e₆(exp η₁,…,exp η₄₅). 여섯 번의 독립 추출로 잘못 계산하지 않는다.</p>
          <p className="mb-0"><b className="text-ink">운영 관문</b> 미공개 회차 Brier·조합 로그점수를 동시에 개선하지 못하면 η=0으로 되돌린다. 인기 회피는 공동당첨 위험용 별도 점수다.</p>
        </div>
      </details>

      <footer className="mt-8 border-t border-rule pt-4 text-[11.5px] leading-[1.85] text-ink3">
        <p className="m-0">출처 <a className="text-signal" href={d.official_source}>동행복권 공식 회차 결과</a> · 연구/검증 목적 · 구매·당첨 보장 기능 없음</p>
        <p className="m-0">19세 미만은 복권을 구매할 수 없다. 구매 예산과 손실 한도는 모델 밖에서 먼저 정한다.</p>
      </footer>
    </Shell>
  );
}

function Shell({ children }) {
  return <div className="mx-auto max-w-[900px] px-5 pb-20"><Nav current="lotto.html" />{children}<ThemeToggle /></div>;
}

function Metric({ label, value }) {
  return <Card className="px-3.5 py-3"><div className="tick">{label}</div><div className="mt-0.5 text-[14px] font-semibold leading-snug">{value}</div></Card>;
}

function Combination({ row, index }) {
  const reasons = row.popularity_risk?.reasons || [];
  return (
    <Card className="px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="tnum text-[11px] text-ink3">{String.fromCharCode(65 + index)}</span>
        <span className="text-[10.5px] text-ink3">중복거리 {row.min_distance_from_previous}/6</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">{row.numbers.map((n) =>
        <span key={n} className="tnum inline-flex h-8 w-8 items-center justify-center rounded-full border border-rule bg-paper text-[12px] font-semibold">{n}</span>)}</div>
      <div className="mt-2 text-[10.5px] text-ink3">인기위험 {Number(row.popularity_risk?.score || 0).toFixed(1)} · {reasons.length ? reasons.join(" · ") : "탐지된 전형 패턴 없음"}</div>
    </Card>
  );
}

function AuditCell({ label, stat, p }) {
  return <div><div className="tick">{label}</div><div className="tnum mt-0.5 text-[16px] font-semibold">{Number(stat).toFixed(3)}</div><div className="tnum text-[11px] text-ink3">보정 p={pvalue(p)}</div></div>;
}

function FutureCell({ symbol, title, text }) {
  return <div className="border-l-2 border-rule pl-3"><div className="tnum text-[16px] font-semibold">{symbol}</div><div className="text-[12.5px] font-semibold">{title}</div><div className="mt-1 text-[11.5px] leading-[1.65] text-ink3">{text}</div></div>;
}

function KeyValue({ k, v, mono }) {
  return <div className="grid gap-1 border-b border-rule2 py-1.5 sm:grid-cols-[150px_1fr]"><span className="text-ink3">{k}</span><span className={`${mono ? "tnum break-all text-[10.5px]" : ""}`}>{v || "—"}</span></div>;
}

function modelLabel(name) {
  return ({ uniform: "균등", bayesian_bias: "베이지안 축소", future_constant: "미래상수 통합" })[name] || name;
}
