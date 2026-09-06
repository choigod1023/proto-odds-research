import GameInfoModal from "./GameInfoModal.jsx";

export default function SelectionReview({ items, onRemove, onSave, onClose }) {
  return <GameInfoModal title="내 선택 목록" suffix="" onClose={onClose}>
    <p className="review-note">선택 당시 배당을 보관합니다. 각 항목을 확인한 뒤 구매 배당과 금액을 입력해 내 기록에 저장하세요.</p>
    <p className="review-note">아직 저장하지 않은 선택은 이 화면을 떠나면 사라집니다.</p>
    {!items.length && <p>모두 확인했습니다. <a href="dashboard.html">내 기록에서 결과 추적하기 →</a></p>}
    <ul className="selection-list">{items.map((item) => <li key={item.key}>
      <span className="selection-badge">✓ 내 선택</span>
      <h3>{item.game.home} vs {item.game.away}</h3>
      <p>{item.option.market} {item.option.label} · <b>{item.option.선택}</b> · {Number(item.option.배당).toFixed(2)}배</p>
      <small>선택 시각 {new Date(item.selectedAt).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} KST</small>
      <div className="selection-actions"><button type="button" onClick={() => onRemove(item.key)}>제거</button>
        <button type="button" className="primary-action" onClick={() => onSave(item)}>확인하고 기록 저장</button></div>
    </li>)}</ul>
  </GameInfoModal>;
}
