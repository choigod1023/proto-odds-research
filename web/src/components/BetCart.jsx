import { useEffect, useMemo, useState } from "react";
import { cartPayout } from "../lib/bet-cart.js";

const STORAGE_KEY = "proto-manual-bet-cart-v1";
const money = (value) => `${Math.round(value).toLocaleString("ko-KR")}원`;

function readStake() {
  try {
    const value = Number(localStorage.getItem(`${STORAGE_KEY}-stake`));
    return Number.isFinite(value) && value >= 100 ? value : 10_000;
  } catch {
    return 10_000;
  }
}

export default function BetCart({ items, onRemove, onClear }) {
  const [stake, setStake] = useState(readStake);
  const result = useMemo(() => cartPayout(items, stake), [items, stake]);

  useEffect(() => {
    localStorage.setItem(`${STORAGE_KEY}-stake`, String(stake));
  }, [stake]);

  return (
    <section className="bet-cart" aria-label="내 배팅 장바구니">
      <div className="bet-cart-head">
        <div><small>내 배팅</small><h3>{items.length ? `${items.length}폴더 조합` : "경기에서 배당을 담아주세요"}</h3></div>
        {items.length > 0 && <button type="button" onClick={onClear}>전체 비우기</button>}
      </div>
      {items.length > 0 && <div className="bet-cart-items">
        {items.map((item) => <div key={item.id}>
          <span><b>{item.home} vs {item.away}</b><small>{item.market}{item.label ? ` ${item.label}` : ""} · {item.selection}</small></span>
          <strong>{Number(item.odds).toFixed(2)}</strong>
          <button type="button" aria-label={`${item.home} 대 ${item.away} 선택 삭제`} onClick={() => onRemove(item.id)}>×</button>
        </div>)}
      </div>}
      <div className="bet-cart-summary">
        <label>투입금<span><input aria-label="배팅 투입금" type="number" min="100" step="100" value={stake}
          onChange={(event) => setStake(Math.max(100, Number(event.target.value) || 100))} />원</span></label>
        <div><small>폴더</small><b>{items.length}폴더</b></div>
        <div><small>조합 배당</small><b>{items.length ? `${result.combined.toFixed(2)}배` : "-"}</b></div>
        <div><small>적중 시 받는 금액</small><b>{items.length ? money(result.gross) : "-"}</b></div>
        <div><small>예상 순이익</small><b>{items.length ? `+${money(result.profit)}` : "-"}</b></div>
      </div>
    </section>
  );
}
