import GameInfoModal from "./GameInfoModal.jsx";
import { useState } from "react";
import { createSelectionRecord } from "../lib/selection-record.js";
import { upsertBet } from "../lib/bet-ledger.js";

export default function BetSaveDialog({ draft, onClose, onSaved }) {
  const [stake, setStake] = useState(10000);
  const [purchaseOdds, setPurchaseOdds] = useState(Number(draft?.option?.["배당"]) || 0);
  const [error, setError] = useState("");
  if (!draft) return null;
  const save = () => {
    const record = createSelectionRecord(draft, { stake, purchaseOdds });
    try { upsertBet(record); } catch { setError("저장하지 못했습니다. 기기 저장 공간을 확인한 뒤 다시 시도하세요."); return; }
    onSaved?.(record);
    onClose();
  };
  return (
    <GameInfoModal title="기록 저장" suffix="" onClose={onClose}>
      <section className="bet-dialog">
        <div><small>단일 베팅 기록</small><h2 id="bet-dialog-title">{draft.game.home} vs {draft.game.away}</h2></div>
        <p>{draft.option.market}{draft.option.label ? ` ${draft.option.label}` : ""} · <b>{draft.option["선택"]}</b></p>
        <label>구매 배당<input type="number" min="1.01" step="0.01" value={purchaseOdds}
          onChange={(event) => setPurchaseOdds(event.target.value)} /></label>
        <label>투입금<input type="number" min="100" step="100" value={stake}
          onChange={(event) => setStake(event.target.value)} /></label>
        <p className="review-note">이 기기에 저장됩니다. 선택 당시 확률·배당과 이후 경기 결과를 구분해 추적합니다.</p>
        {error && <p role="alert">{error}</p>}
        <div className="bet-dialog-actions">
          <button type="button" onClick={onClose}>취소</button>
          <button type="button" className="is-primary" disabled={!(purchaseOdds > 1 && stake > 0)} onClick={save}>내 기록에 저장</button>
        </div>
      </section>
    </GameInfoModal>
  );
}
