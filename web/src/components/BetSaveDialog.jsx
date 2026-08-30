import { useState } from "react";
import { createBetRecord, upsertBet } from "../lib/bet-ledger.js";

export default function BetSaveDialog({ draft, onClose, onSaved }) {
  const [stake, setStake] = useState(10000);
  const [purchaseOdds, setPurchaseOdds] = useState(Number(draft?.option?.["배당"]) || 0);
  if (!draft) return null;
  const save = () => {
    const record = createBetRecord(draft.game, draft.option, { stake, purchaseOdds });
    upsertBet(record);
    onSaved?.(record);
    onClose();
  };
  return (
    <div className="bet-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="bet-dialog" role="dialog" aria-modal="true" aria-labelledby="bet-dialog-title">
        <div><small>단일 베팅 기록</small><h2 id="bet-dialog-title">{draft.game.home} vs {draft.game.away}</h2></div>
        <p>{draft.option.market}{draft.option.label ? ` ${draft.option.label}` : ""} · <b>{draft.option["선택"]}</b></p>
        <label>구매 배당<input type="number" min="1.01" step="0.01" value={purchaseOdds}
          onChange={(event) => setPurchaseOdds(event.target.value)} /></label>
        <label>투입금<input type="number" min="100" step="100" value={stake}
          onChange={(event) => setStake(event.target.value)} /></label>
        <div className="bet-dialog-actions">
          <button type="button" onClick={onClose}>취소</button>
          <button type="button" className="is-primary" disabled={!(purchaseOdds > 1 && stake > 0)} onClick={save}>내 베팅에 저장</button>
        </div>
      </section>
    </div>
  );
}
