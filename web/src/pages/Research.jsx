import { Card, Nav, ThemeToggle } from "../components/ui.jsx";

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
const DIALECTIC = [
  {
    mark: "정",
    title: "경기 정보는 시장에 없는 신호를 보탠다",
    body: "선수 능력, 실제 선발, 부상·복귀, 불펜 소모와 최근 경기력은 팀 평균만으로 사라지는 변화를 포착할 수 있다.",
    test: "시장 확률에 팀·선수 변수를 추가했을 때 시계열 표본 밖 Brier와 로그손실이 함께 개선되는가",
  },
  {
    mark: "반",
    title: "배당은 이미 강한 집단 예측이다",
    body: "복잡한 모델의 적중률이 높아져도 마진, 데이터 누수, 과적합을 넘지 못할 수 있다. 우연한 한 시즌 수익은 증거가 아니다.",
    test: "종가보다 앞선 시점의 정보만 사용해 리그·시즌을 바꿔도 개선과 CLV가 반복되는가",
  },
  {
    mark: "합",
    title: "시장을 기준점으로 두고 수정분만 검증한다",
    body: "모델이 승패를 처음부터 다시 맞히게 하지 않는다. 시장 확률을 출발점으로 삼고 새 정보가 만든 확률 변화만 측정한다.",
    test: "시장 → 팀 → 선수·라인업 → 텍스트의 순서로 한 단계씩 추가해 순수 기여도를 분리한다",
  },
];

const EXPERIMENT_LADDER = [
  ["M0", "시장 기준", "마진을 제거한 프로토·동시점 해외 배당", "비교 기준"],
  ["M1", "팀 경기력", "최근 득실마진·홈원정·휴식·상대 강도", "M0 대비 개선"],
  ["M2", "선수·라인업", "선발, 결장·복귀, 최근 타격, 불펜 소모, 출전 시간", "M1 대비 개선"],
  ["M3", "텍스트·LLM", "공식 발표와 기사에서 구조화한 변화 신호", "M2 대비 개선"],
];

const RESEARCH_SOURCES = [
  ["확률 보정이 정확도보다 중요", "Walsh & Joshi, Machine Learning with Applications (2024)", "https://doi.org/10.1016/j.mlwa.2024.100539"],
  ["선수 능력을 포함한 베이지안 축구 모델", "Whitaker et al., JRSS Series C (2021)", "https://doi.org/10.1111/rssc.12454"],
  ["배당과 다른 신호·포트폴리오 위험", "Hubáček et al., International Journal of Forecasting (2019)", "https://doi.org/10.1016/j.ijforecast.2019.01.001"],
  ["시장 가격은 새 정보를 빠르게 반영", "Croxson & Reade, The Economic Journal (2014)", "https://doi.org/10.1111/ecoj.12033"],
  ["운의 비중이 복잡한 모델의 상한을 만든다", "Aoki et al., KDD (2017)", "https://doi.org/10.1145/3097983.3098045"],
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
          연구 설계 추가 2026-08-24 · 실측 경기 43,509 ·{" "}
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
        <b className="text-[13.5px] text-sev3">1차 결론 — 현재 데이터에서 확인된 것</b>
        <p className="mt-1.5 mb-2.5 text-[13px] leading-[1.75] text-ink2">
          <b className="text-ink">현재 검증한 규칙만으로는 시장을 이기지 못했다.</b> 새 정보는 같은 기준으로 다시 반증한다.
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

      <H2 n="01">다음 검증 — 시장에서 시작해 정보의 값을 분리한다</H2>
      <Lead>
        새 모델이 과거 결론을 뒤집을 수는 있다. 단, “더 그럴듯해졌다”가 아니라
        <b> 어느 정보가 표본 밖 확률을 얼마나 개선했는지</b>로 판정한다.
      </Lead>
      <div className="grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]">
        {DIALECTIC.map((item) => (
          <Card key={item.mark} className="px-4 py-4">
            <div className="mb-2 flex items-center gap-2">
              <span className="tnum flex size-7 items-center justify-center rounded-full border border-rule text-[12px] font-semibold">{item.mark}</span>
              <b className="text-[13px]">{item.title}</b>
            </div>
            <p className="m-0 text-[12px] leading-[1.75] text-ink2">{item.body}</p>
            <p className="mt-3 mb-0 border-t border-rule2 pt-2.5 text-[11px] leading-[1.7] text-ink3">
              <b className="text-ink2">판정 질문</b> {item.test}
            </p>
          </Card>
        ))}
      </div>

      <H3>한 번에 하나씩 추가하는 네 단계</H3>
      <Table head={["단계", "모델", "사용 정보", "판정"]} rows={EXPERIMENT_LADDER} />
      <Card className="mt-3 border-l-[3px] border-l-sev2 px-4 py-3.5">
        <div className="text-[13px] font-semibold">미래 불확실성은 임의 난수가 아니라 시나리오로 넣는다</div>
        <p className="mt-1.5 mb-0 text-[12.5px] leading-[1.75] text-ink2">
          선발 출전 70%·결장 30%처럼 관측 가능한 불확실성을 각각 시뮬레이션하고 합친다.
          데이터 지연, 라인업 미확정, 복귀 후 경기력은 서로 다른 불확실성으로 기록한다.
          LLM은 확률을 직접 만들지 않고 공식 발표와 기사에서 이 상태를 구조화하고 설명한다.
        </p>
      </Card>

      <H3>통과 기준</H3>
      <Table head={["지표", "무엇을 막는가", "판정 방식"]}
        rows={[["Brier·로그손실", "적중률만 높이는 과신", "M0보다 두 지표 모두 개선"],
               ["Calibration", "60% 예측이 실제 45%인 문제", "확률 구간별 오차와 ECE"],
               ["CLV", "결과 운으로 생긴 단기 수익", "예측 시점 대비 마감 확률 우위"],
               ["ROI·최대낙폭", "수익만 보고 위험을 숨기는 문제", "회차 순서 그대로 walk-forward"],
               ["재현성", "한 리그·한 시즌 과적합", "리그와 시즌을 바꿔 방향 반복"]]} />
      <P>
        M1이 실패하면 팀 변수는 중단한다. M2가 성공하면 어떤 선수군에서 개선됐는지 다시 분리한다.
        M3는 M2를 넘어설 때만 예측 입력으로 인정하며, 그렇지 않으면 경기력 설명에만 사용한다.
      </P>
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

      <H2 n="02">지금까지 만난 열두 개의 벽</H2>
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

      <H2 n="03">확정된 사실</H2>
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

      <H2 n="04">스스로 잡은 오류</H2>
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

      <H2 n="05">지금 돌아가는 것</H2>
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

      <H2 n="06">현실 점검</H2>
      <P>구매 한도가 <b>회차당 10만원</b>이다. 엣지 3%를 확보해도 회차당 3,000원,
        연 150회차면 45만원이 구조적 천장이다. 그리고 그 3%를 실제로 확보하는 것 자체가 대부분 실패한다.</P>
      <P>열두 번 검증했고 <b>실전에 쓸 수 있는 +EV 는 확인되지 않았다.</b>
        결과가 "시장은 효율적이다"로 나와도 그건 실패가 아니라 결론이다.</P>

      <H2 n="07">이번 검증을 지탱하는 근거</H2>
      <div className="divide-y divide-rule2 border-y border-rule">
        {RESEARCH_SOURCES.map(([title, paper, href]) => (
          <a key={href} href={href} className="grid gap-1 py-3 text-inherit no-underline sm:grid-cols-[210px_1fr]">
            <b className="text-[12px]">{title}</b>
            <span className="text-[11.5px] text-ink3">{paper}</span>
          </a>
        ))}
      </div>
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
