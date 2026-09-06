import { Nav } from "../components/ui.jsx";

const STEPS = [
  ["경기와 배당을 확인합니다", "종목·리그·경기 시각과 마켓을 맞추고, 유효한 배당이 있는 선택지를 비교합니다. 경기 상태나 데이터가 불명확하면 확인이 필요하다고 표시합니다."],
  ["같은 마켓의 선택지를 비교합니다", "배당에서 계산한 시장 기준 확률을 출발점으로 삼아 예상 적중확률이 높은 선택을 찾습니다. 표시되는 확률은 적중을 보장하는 수치가 아닙니다."],
  ["경기력 근거를 함께 보여줍니다", "제공되는 최근 성적, 득점·실점, 홈·원정 성적과 선수·출전 정보를 살펴볼 수 있습니다. 설명에 쓰이는 정보와 실제 확률 계산에 반영된 정보는 구분합니다. 자료가 없으면 추측으로 채우지 않습니다."],
  ["선택 당시 기록을 남깁니다", "경기 전에 제시한 픽·배당·확률을 기록합니다. 경기가 시작된 뒤 유리해진 선택으로 과거 기록을 바꾸지 않으며, 내가 저장한 선택은 내 기록에서 확인할 수 있습니다."],
  ["진행 상황과 결과를 따로 확인합니다", "진행 중에는 점수와 경기 상황을 바탕으로 현재 확률을 추정합니다. 종료 후에는 당시 픽에 결과를 연결해 적중 여부를 확인합니다."],
];
export default function Research() {
  return <main className="pick-method-page mx-auto max-w-[900px] px-5 pb-24">
    <Nav current="research.html" />
    <header className="market-header"><div><h1>우리는 이렇게 픽을 선택합니다</h1><p>무엇을 비교하고, 어떤 근거를 보여주며, 결과를 어떻게 확인하는지 안내합니다.</p></div></header>
    <ol className="pick-method-steps">{STEPS.map(([title,body],i)=><li key={title}><span aria-hidden="true">{String(i+1).padStart(2,"0")}</span><div><h2>{title}</h2><p>{body}</p></div></li>)}</ol>
    <section id="ai-model" className="pick-method-notes"><h2>새로운 분석은 검증 후 반영합니다</h2><p>AI나 선수 분석으로 만든 수치도 검증 없이 추천 확률에 더하지 않습니다. 과거에 맞춘 결과뿐 아니라, 이후 경기에서도 같은 방식이 통하는지 확인합니다.</p></section>
    <section className="pick-method-notes"><h2>적중률만으로 평가하지 않습니다</h2><p>예측을 남긴 경기 수, 예상 확률과 실제 결과의 차이, 배당을 고려한 성과를 함께 봅니다. 경기 이후에 알게 된 정보는 당시 예측의 근거로 사용하지 않습니다.</p><a href="dashboard.html">내 기록에서 선택과 결과 확인하기 →</a></section>
  </main>;
}
