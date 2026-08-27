import { Card, Nav, ThemeToggle } from "../components/ui.jsx";
import { AiMethodology } from "../components/AiDisclosure.jsx";

/* 정적 문서다. 데이터는 안 읽는다 — 여기 적힌 숫자는 findings/ 문서의 확정값이다. */

const WALLS = [
  ["배당 구조만으로 +EV 구간이 있는가", "배당대·종목·상품·리그로 134개 구간을 쪼개 검정", "0개", "neg"],
  ["팀 단위 변수가 시장을 넘는가", "폼·득실마진·홈원정·백투백·휴식·상대전적 9종", "시장 우위", "neg"],
  ["모델을 정교화하면 좁혀지는가", "점수차 기반 pi-ratings 도입", "15%만 축소", "warn"],
  ["선발 정보 시차가 우위인가", "KBO·MLB·NPB 선발 예고 시점 대조", "재현 실패", "neg"],
  ["축구 라인업이 우위인가", "라인업은 경기 1시간 전 공개 = 배당보다 확실히 늦음", "표본 부족", "warn"],
  ["해외 배당과의 괴리에 기회가 있는가", "509경기·1,120선택지 대조. 괴리는 있으나 마감배당(미래정보)이었다", "0.9%", "neg"],
  ["구장 파크팩터가 우위인가", "walk-forward 구장 효과는 진짜(우위확률 97.9%). 그런데 시장 확률 위에 얹으니 오히려 나빠졌다", "시장이 안다", "neg"],
  ["타선을 선수 단위로 재면 달라지는가", "2,896경기 선수별 재수집. 방향은 일관(계수 +8~9)이나 크기가 1.7%p", "44시즌 필요", "neg"],
  ["프로토가 약한 리그가 있는가", "리그 × 마켓 74조합. 커버가 얇은 리그일수록 오히려 더 진다", "0개", "neg"],
  ["얇은 시장이 기회인가", "초대면 −1.89% → 16회+ +0.10%. 단조인데 방향이 반대", "기각", "neg"],
  ["배당이 얼어 있으니 정보 시차가 있지 않은가", "프로토 라인의 86.2%가 33시간 동안 안 움직인다 — 전제는 참. 그런데 샤프 마켓조차 24시간 스윙이 중앙 2.4%p", "정보가 작다", "neg"],
  ["구조적 선택으로 마진을 낮출 수 있는가", "규칙 7개를 전부 AND. 표본은 5.5배 줄었는데 얻은 건 2.4%p", "−13.9%→−11.5%", "warn"],
];

const MISTAKES = [
  ["언더오버 배당 순서를 반대로 읽음", "같은 구간의 오버·언더가 둘 다 +ROI 로 나오는 물리적 모순", "전체의 26%(46,088행) 오염. ROI +54% 라는 가짜 결과"],
  ["같은 경기가 여러 회차에 중복 발매되는 걸 놓침", "2026 LG 시즌 성적이 196경기(실제 144)로 집계", "Elo 누수. 픽 ROI 가 −8.6% 로 좋아 보였으나 실제 −15.1%"],
  ["pi-ratings 파라미터를 감으로 지정", "농구 Brier 가 Elo 보다 0.026 악화", "프로젝트 원칙 위반. 학습 구간 격자탐색으로 재설정"],
  ["3-way 를 2-way 로직으로 계산", "마진 12% 시장에서 ROI +64% 라는 불가능한 값", "+EV 비율 21.6% → 실제 0.9%"],
];

export default function Research() {
  return (
    <div className="mx-auto max-w-[860px] px-5 pb-20">
      <Nav current="research.html" />
      <header>
        <h1 className="mt-[22px] mb-1 text-[22px] leading-snug tracking-[-.01em] text-balance">
          한국 프로토 고정배당 시장은 효율적인가
        </h1>
        <p className="m-0 text-[13.5px] text-ink2">
          553회차·353,047건을 실측해 답을 찾는 공개 연구. 결과가 어느 쪽이든 그대로 적는다.
        </p>
        <div className="mt-1.5 text-[11.5px] text-ink3">
          최종 갱신 2026-07-29 · 실측 경기 43,509 ·{" "}
          <a className="text-signal" href="https://github.com/choigod1023/proto-odds-research/tree/main/findings">
            전체 문서
          </a>
        </div>
      </header>

      <Card className="mt-4 flex flex-wrap gap-x-10 gap-y-3 px-4 py-3.5">
        {[["수집 회차", "553", "2023–2026"],
          ["베팅 레코드", "353,047", "게임행 177,549"],
          ["프로토 환급률", "88.0%", "2-way 기준"],
          ["해외 환급률", "95.3%", "7.3%p 차이"]].map(([k, v, n]) => (
          <div key={k}>
            <div className="tick">{k}</div>
            <div className="tnum text-[19px] font-semibold leading-[1.35]">{v}</div>
            <div className="text-[11px] text-ink3">{n}</div>
          </div>
        ))}
      </Card>

      {/* 결론 — 이 페이지의 논지 */}
      <Card className="mt-4 border-l-[3px] border-l-sev3 px-4 py-4">
        <b className="text-[13.5px] text-sev3">현재 운영 결론 — 기존 신호는 승격하지 않는다</b>
        <p className="mt-1.5 mb-2.5 text-[13px] leading-[1.75] text-ink2">
          <b className="text-ink">현재 검증한 신호로는 시장을 이기지 못했다.</b> 새 신호도 미래 구간에서 입증되기 전에는 운영에 넣지 않는다.
        </p>
        <table className="w-auto border-collapse text-[13px]">
          <tbody>
            {[["필요한 우위", "6.8%p (2-way 마진 12%)"],
              ["정보의 크기", "2.4%p — 샤프 마켓 자신의 24h 스윙"],
              ["구조 선택의 이득", "2.4%p — 규칙 7개 전부 누적"],
              ["합쳐도", "4.8%p < 6.8%p"]].map(([k, v], i) => (
              <tr key={k}>
                <td className="py-0.5 pr-4 text-ink3">{k}</td>
                <td className={`tnum py-0.5 ${i === 3 ? "font-semibold text-ink" : ""}`}>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2.5 mb-0 text-[13px] leading-[1.75] text-ink2">
          문헌이 이걸 확증한다 — 장기 수익 베터는 약 3%, 프로가 효율 시장에서 목표하는 CLV 는{" "}
          <b className="text-ink">+2~3%</b>, 라인이 무른 틈새시장에서도 <b className="text-ink">+5%</b> 다.{" "}
          <b className="text-ink">프로토 마진 12% 는 세계 최고의 엣지를 그대로 가져와도 −7% 가 되는 크기다.</b>{" "}
          Aoki et al.(KDD 2017)도 "운이 상당해서 정교한 모델이 단순 모델을 거의 못 이긴다"고 한다.
        </p>
      </Card>

      <AiMethodology id="ai-model" showLink={false} />
      <Card className="mt-3 border-l-[3px] border-l-signal px-4 py-4">
        <b className="text-[13.5px]">AI가 확률을 바꿀 수 있는 유일한 경로</b>
        <p className="mt-1.5 mb-2 text-[13px] leading-[1.75] text-ink2">
          운영식은 <b className="tnum text-ink">logit(p최종) = logit(p시장) + AI 잔차</b>다.
          AI는 시장이 이미 아는 팀 전력 전체를 다시 예측하지 않고, 같은 시각 이후 새로 확인된
          선발·결장·라인업 변화만 잔차로 학습한다. 현재 잔차는 검증 전이라 <b className="text-ink">0%p</b>다.
        </p>
        <ol className="m-0 grid gap-2 p-0 text-[12px] leading-[1.65] text-ink2 sm:grid-cols-2">
          <li className="list-none border-t border-rule2 pt-2"><b className="text-ink">1. 시각 고정</b><br />예측 시점에 실제로 보인 배당·자료만 저장한다.</li>
          <li className="list-none border-t border-rule2 pt-2"><b className="text-ink">2. 워크포워드</b><br />과거로 학습하고 그다음 경기만 예측한다.</li>
          <li className="list-none border-t border-rule2 pt-2"><b className="text-ink">3. 확률 검정</b><br />Brier·로그손실·보정도를 시장 기준선과 비교한다.</li>
          <li className="list-none border-t border-rule2 pt-2"><b className="text-ink">4. 승격 또는 0</b><br />미공개 기간 개선의 신뢰구간이 0을 넘을 때만 반영한다.</li>
        </ol>
        <p className="mt-2.5 mb-0 text-[11px] leading-[1.7] text-ink3">
          생성형 AI는 기사·공식 발표를 구조화하고 문장을 다듬을 뿐, 직접 확률을 쓰지 않는다. 선택적 예측으로
          자료가 낡았거나 분포가 바뀐 경기는 보류하고, 고정된 커버리지에서 성능을 비교한다.{" "}
          <a className="text-signal" href="https://proceedings.mlr.press/v70/guo17a.html">확률 보정</a> ·{" "}
          <a className="text-signal" href="https://proceedings.mlr.press/v97/geifman19a.html">선택적 예측</a> ·{" "}
          <a className="text-signal" href="https://journals.sagepub.com/doi/10.1177/1471082X20929881">선수·팀 정보 결합</a>
        </p>
      </Card>

      <Card id="evolutionary-selector" className="mt-3 scroll-mt-4 border-l-[3px] border-l-rule px-4 py-4">
        <b className="text-[13.5px]">자연선택 추천기 — 확률을 바꾸지 않고 한 픽을 고르는 AI</b>
        <p className="mt-1.5 mb-2 text-[13px] leading-[1.75] text-ink2">
          전략 56개를 한 세대로 두고 24세대 동안 교배·돌연변이시킨다. 적중률만 좇아
          초저배당으로 퇴화하지 않도록 유형별 최소배당·추천 빈도·종목 쏠림을 함께 생존 조건으로 둔다.
          2023–2024에서 진화하고 2025에서 생존 전략을 고른 뒤 2026의 하루 한 픽으로 역사 감사했다.
        </p>
        <ol className="mb-3 grid list-none gap-2 p-0 text-[11px] leading-[1.6] text-ink2 sm:grid-cols-4">
          {[
            ["01", "후보 생성", "서로 다른 가중치 전략 56개"],
            ["02", "진화", "상위 전략 교배·돌연변이 24세대"],
            ["03", "생존 선택", "2025 성능으로 최종 전략 선택"],
            ["04", "감사", "2026 하루 한 픽으로 재검사"],
          ].map(([number, title, description]) => (
            <li key={number} className="rounded border border-rule2 bg-panel px-2.5 py-2">
              <span className="tnum text-ink3">{number}</span>{" "}<b className="text-ink">{title}</b><br />
              {description}
            </li>
          ))}
        </ol>
        <div className="grid gap-2 text-[12px] sm:grid-cols-3">
          {[
            ["안정형", "77.9% · 평균 1.21배", "2025 +0.6 → 2026 −0.9%p", "−6.1~+4.3%p"],
            ["균형형", "62.8% · 평균 1.45배", "2025 −0.6 → 2026 +6.1%p", "−1.7~+13.9%p"],
            ["도전형", "48.5% · 평균 1.75배", "2025 +1.7 → 2026 −6.1%p", "−14.3~+2.2%p"],
          ].map(([name, result, delta, interval]) => (
            <div key={name} className="border-t border-rule2 pt-2">
              <b className="text-ink">{name}</b><br />
              <span className="tnum text-ink2">{result}</span><br />
              <span className="tnum text-sev3">동일 배당범위 기준선 대비 {delta}</span><br />
              <span className="tnum text-[10.5px] text-ink3">95% CI {interval}</span>
            </div>
          ))}
        </div>
        <div className="mt-3 rounded border border-sev2 bg-panel px-3 py-2.5 text-[11.5px] leading-[1.7] text-ink2">
          <b className="text-sev3">운영 결정 · 세 유형 모두 추천 중단</b><br />
          2025와 2026의 개선 방향이 모두 뒤집혔다. 균형형의 2026 +6.1%p만 떼어 쓰지 않으며,
          오늘 화면에서도 탈락 유형은 실제 후보를 내지 않는다.
        </div>
        <p className="mt-2.5 mb-0 text-[11px] leading-[1.7] text-ink3">
          초기 설계를 2026 감사 후 수정했으므로 이 수치는 독립 홀드아웃이 아니다.
          사전등록 미래 300픽 이상에서 동일 날짜·동일 배당 범위 시장확률 1순위보다 적중률 차이의 95% 신뢰구간 하한이 0을 넘을 때만 다시 연다.{" "}
          <a className="text-signal" href="https://github.com/choigod1023/proto-odds-research/blob/main/findings/%EC%9E%90%EC%97%B0%EC%84%A0%ED%83%9D_%EC%B6%94%EC%B2%9C%EA%B8%B0_%EA%B2%80%EC%A6%9D.md">재현 문서 보기 →</a>
        </p>
      </Card>

      {/* 오늘 확인된 정정 — 예전 문서의 −9.2% 는 살 수 없는 숫자였다 */}
      <Card className="mt-3 border-l-[3px] border-l-sev2 px-4 py-4">
        <b className="text-[13.5px] text-sev2">2026-07-29 정정 — 실제로 살 수 있는 값</b>
        <p className="mt-1.5 mb-2.5 text-[13px] leading-[1.75] text-ink2">
          이전까지 "아무거나 −13.7% → 등급 A 만 −9.2%, 4.5%p 절감" 이라고 적었다.
          그런데 그건 <b className="text-ink">선택지 1개 기준</b>이고, 단폴(한경기구매)은
          '한경기' 로 지정된 경기만 살 수 있다. 배당을 올리려면 조합해야 하고,
          <b className="text-ink"> 조합하면 다리마다 마진이 한 번씩 더 물린다.</b>
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[380px] border-collapse text-[13px]">
            <thead>
              <tr>
                {["", "1폴(지정 경기만)", "2폴", "3폴"].map((h) => (
                  <th key={h} className="border-b border-rule2 pb-1 pr-3 text-left text-[11px] font-medium text-ink3">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[["아무거나", "−13.6%", "−25.3%", "−35.5%"],
                ["배당 1.0–1.3 · 2-way", "−9.8%", "−18.7%", "−26.7%"],
                ["절약폭", "3.8%p", "6.6%p", "8.8%p"]].map(([a, b, c, e]) => (
                <tr key={a}>
                  <td className="border-b border-rule2 py-1 pr-3 text-ink2">{a}</td>
                  <td className="tnum border-b border-rule2 py-1 pr-3">{b}</td>
                  <td className="tnum border-b border-rule2 py-1 pr-3 font-semibold">{c}</td>
                  <td className="tnum border-b border-rule2 py-1 pr-3">{e}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2.5 mb-0 text-[13px] leading-[1.75] text-ink2">
          절약폭 자체는 오히려 커진다(4.5%p → 6.6%p). 절대 손실이 두 배가 될 뿐이다.
          그리고 <b className="text-ink">저배당 우선 규칙은 조합에서 뒤집힌다</b> — 배당 1.0–1.3 은
          다리 하나로는 최선이지만 배당을 만드는 효율은 최악이라, 목표 배당이 있으면
          다리당 배당을 올리는 쪽이 덜 잃는다.
        </p>
      </Card>

      <H2 n="01">지금까지 만난 열두 개의 벽</H2>
      <Lead>각 검증의 판정 기준은 <b>데이터를 보기 전에</b> 정해뒀다. 나중에 정하면 어떤 결과든 성공으로 해석되기 때문이다.</Lead>
      <ol className="m-0 list-none p-0">
        {WALLS.map(([t, dsc, r, tone], i) => (
          <li key={t}>
            <Card className="mt-2 flex flex-wrap items-center gap-3 px-3.5 py-3">
              <span className="tnum w-6 shrink-0 text-[12px] text-ink3">{i + 1}</span>
              <span className="min-w-[200px] flex-1">
                <span className="block text-[13.5px] font-semibold">{t}</span>
                <span className="block text-[12px] leading-[1.7] text-ink3">{dsc}</span>
              </span>
              <span className={`tnum shrink-0 text-[12.5px] font-semibold ${tone === "neg" ? "text-sev3" : "text-sev2"}`}>{r}</span>
            </Card>
          </li>
        ))}
      </ol>

      <H2 n="02">확정된 사실</H2>
      <Lead>재현 가능하고, 코드가 공개돼 있다.</Lead>

      <H3>booking 은 상품이 아니라 <b>구조</b>로 결정된다</H3>
      <Table head={["구조", "포함 상품", "환급률", "회차 내부 SD"]}
        rows={[["2-way", "승패·언더오버·2way핸디캡", "88.00%", "0.0006"],
               ["3-way", "승무패·승①패", "87.00%", "0.0007"],
               ["3-way 핸디캡", "—", "85.99%", "0.0008"]]} />
      <P>회차 <b>내부</b> SD 는 0.0007 인데 회차 <b>간</b> SD 는 0.0090 으로 <b>13.5배</b> 다.
        회차마다 목표 환급률이 86/87/88/89% 중 하나로 달라진다.</P>

      <H3>favorite-longshot 편향이 교과서적으로 존재한다</H3>
      <Table head={["배당 구간", "실측 ROI", "기준선 대비"]}
        rows={[["1.0–1.5 (강팀)", "−9.90%", "+2.50%p"],
               ["1.8–2.2 (박빙)", "−12.57%", "−0.16%p"],
               ["5.0+ (극단 역배)", "−33.19%", "−20.04%p"]]} />
      <P>미국 경마(Snowberg &amp; Wolfers)와 <b>같은 방향</b>이다.
        "한국은 인기구단 쏠림으로 반대일 것"이라는 가설은 기각 방향.</P>

      <H3>승패보다 득실 마진이 정보량이 많다</H3>
      <P>축구·농구·배구 <b>세 종목 모두</b> '최근 득실 마진'이 '최근 승률'을 앞섰다.
        같은 5승 5패라도 한 점 차로 이기고 크게 지는 팀은 다르다.
        문헌(pi-ratings)을 읽고 넣은 게 아니라 <b>측정에서 먼저 나왔다.</b></P>

      <H3>선발투수는 지표를 바꾸자 결과가 뒤집혔다</H3>
      <Table head={["지표", "최대 z", "최대 Brier 개선"]}
        rows={[["팀 실점 (불펜 섞임)", "2.58", "+0.00204"],
               ["투수 개인 자책점·이닝", "4.53", "+0.00613"]]} />
      <P><b>"선발투수는 소용없다"가 아니라 "지표가 거칠어서 안 잡혔다"</b>였다.
        흥미롭게도 자책률보다 <b>평균 이닝</b>이 더 강하다 — 얼마나 적게 내주느냐보다{" "}
        <b>얼마나 길게 끌어주느냐</b>가 승패에 직결된다.</P>

      <H2 n="03">스스로 잡은 오류</H2>
      <Lead>연구 과정에서 낸 실수와 발견 경위. 같은 실수를 반복하지 않기 위해 남긴다.</Lead>
      <div className="grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(280px,1fr))]">
        {MISTAKES.map(([t, found, impact], i) => (
          <Card key={t} className="border-l-[3px] border-l-sev3 px-3.5 py-3">
            <div className="text-[13px] font-semibold">{i + 1}. {t}</div>
            <p className="mt-1.5 mb-1 text-[12.5px] leading-[1.7] text-ink2">
              <span className="text-ink3">발견</span> {found}
            </p>
            <p className="m-0 text-[12.5px] leading-[1.7] text-ink2">
              <span className="text-ink3">영향</span> {impact}
            </p>
          </Card>
        ))}
      </div>
      <blockquote className="mt-3 border-l-[3px] border-rule pl-3.5 text-[13px] leading-[1.8] text-ink2">
        <b className="text-ink">교훈: 결과가 "너무 좋으면" 버그다.</b> 물리적으로 불가능한 값
        (양쪽 다 +EV, ROI +54%, +64%)이 매번 오류를 잡아냈다. 검증 장치를 먼저 만들어 둔 것이
        실제로 작동했다. 지금은 자기검사 9종이 매 실행마다 돈다.
      </blockquote>

      <H2 n="04">지금 돌아가는 것</H2>
      <Lead>남은 질문은 하나다 — <b>배당이 굳은 뒤 공개되는 정보에 기회가 있는가.</b>
        과거 데이터로는 답할 수 없어 실시간으로 쌓는 중이다.</Lead>
      <Table head={["수집기", "주기", "무엇을"]}
        rows={[["프로토 배당 스냅샷", "15분", "발매 중 배당이 실제로 변하는가"],
               ["해외 배당 스냅샷", "15분", "같은 시점 해외 배당 (동시점 대조)"],
               ["선발 정보 관측기", "30분", "KBO·MLB·NPB 선발 예고 시각"]]} />
      <Card className="mt-3 border-l-[3px] border-l-sev2 px-3.5 py-3">
        <div className="text-[13px] font-semibold">왜 실시간이어야 하나</div>
        <p className="mt-1.5 mb-0 text-[12.5px] leading-[1.75] text-ink2">
          과거 해외 배당은 <b className="text-ink">마감 배당</b>이다. 경기 상세의 기록 시각으로
          확인했다 — 경기 약 7시간 전 값이다. 프로토는 최대 60시간 전에 굳으므로{" "}
          <b className="text-ink">베팅 시점에 알 수 없는 값</b>이고, 그걸로 계산한 +EV 에는
          낙관 편향이 섞인다.
        </p>
      </Card>

      <H2 n="05">현실 점검</H2>
      <P>구매 한도가 <b>회차당 10만원</b>이다. 엣지 3%를 확보해도 회차당 3,000원,
        연 150회차면 45만원이 구조적 천장이다. 그리고 그 3%를 실제로 확보하는 것 자체가 대부분 실패한다.</P>
      <P>열두 번 검증했고 <b>실전에 쓸 수 있는 +EV 는 확인되지 않았다.</b>
        결과가 "시장은 효율적이다"로 나와도 그건 실패가 아니라 결론이다.</P>

      <footer className="mt-9 border-t border-rule pt-4 text-[11.5px] leading-[1.85] text-ink3">
        <p className="m-0">데이터 출처 와이즈토토 회차 아카이브 · 네이버 스포츠 · BetExplorer · 비상업 연구 목적</p>
        <p className="m-0">합법 발매처는 오프라인 판매점과 betman.co.kr 뿐이며, 해외 북메이커 이용은 국민체육진흥법 위반이다.</p>
        <p className="m-0 mt-2">
          <a className="text-signal" href="https://github.com/choigod1023/proto-odds-research">소스·방법론·재현 코드</a>
        </p>
      </footer>
      <ThemeToggle />
    </div>
  );
}

const H2 = ({ n, children }) => (
  <h2 className="mt-9 mb-2 flex items-baseline gap-2.5 text-[16px] tracking-[-.01em]">
    <span className="tnum text-[12px] font-normal text-ink3">{n}</span>
    {children}
  </h2>
);
const H3 = ({ children }) => (
  <h3 className="mt-6 mb-1.5 text-[13.5px] font-semibold">{children}</h3>
);
const Lead = ({ children }) => (
  <p className="mt-0 mb-3 text-[13px] leading-[1.8] text-ink2">{children}</p>
);
const P = ({ children }) => (
  <p className="mt-2 mb-0 text-[13px] leading-[1.8] text-ink2">{children}</p>
);

function Table({ head, rows }) {
  return (
    <div className="overflow-x-auto">
      <table className="mt-1.5 w-full min-w-[340px] border-collapse text-[12.5px]">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={h} className={`border-b border-rule pb-1.5 pr-3 text-[11px] font-medium text-ink3 ${i ? "text-right" : "text-left"}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r[0]}>
              {r.map((c, i) => (
                <td key={i} className={`border-b border-rule2 py-1.5 pr-3 ${i ? "tnum text-right" : ""}`}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
