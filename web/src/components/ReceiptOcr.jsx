import { useState } from "react";
import { createBetRecord, upsertBet } from "../lib/bet-ledger.js";
import { receiptMatches } from "../lib/receipt-ocr.js";

export default function ReceiptOcr({ games = [], onImported }) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [text, setText] = useState("");
  const [matches, setMatches] = useState([]);
  const [error, setError] = useState("");

  const scan = async (file) => {
    if (!file) return;
    setBusy(true); setProgress(0); setError(""); setMatches([]);
    let worker;
    try {
      const { createWorker } = await import("tesseract.js");
      worker = await createWorker("kor+eng", 1, {
        logger: (message) => {
          if (message.status === "recognizing text") setProgress(Math.round((message.progress || 0) * 100));
        },
      });
      const result = await worker.recognize(file, { rotateAuto: true });
      const recognized = result?.data?.text || "";
      setText(recognized);
      setMatches(receiptMatches(recognized, games).map((row) => ({ ...row, selected: true })));
    } catch (cause) {
      setError(`사진을 읽지 못했습니다: ${cause?.message || cause}`);
    } finally {
      await worker?.terminate?.();
      setBusy(false);
    }
  };

  const importSelected = () => {
    const selected = matches.filter((row) => row.selected);
    selected.forEach((row) => upsertBet(createBetRecord(row.game, row.option, {
      stake: row.stake, purchaseOdds: row.purchaseOdds,
    })));
    onImported?.(selected.length);
    setMatches([]); setText("");
  };

  const update = (key, patch) => setMatches((current) => current.map((row) => row.key === key ? { ...row, ...patch } : row));
  return (
    <section className="receipt-ocr">
      <div className="receipt-ocr-head">
        <div><small>사진 자동 입력</small><h2>프로토 영수증 OCR</h2></div>
        <label className={busy ? "is-disabled" : ""}>사진 선택·촬영
          <input type="file" accept="image/*" capture="environment" disabled={busy}
            onChange={(event) => scan(event.target.files?.[0])} />
        </label>
      </div>
      <p>게임번호·선택·배당·금액을 읽어 현재 발매 데이터와 일치하는 항목만 후보로 만듭니다. 저장 전 반드시 확인하세요.</p>
      {busy && <div className="receipt-ocr-progress"><i style={{ width: `${progress}%` }} /><span>문자 인식 {progress}%</span></div>}
      {error && <div className="receipt-ocr-error">{error}</div>}
      {!busy && text && !matches.length && <div className="receipt-ocr-error">일치하는 발매 선택지를 찾지 못했습니다. 사진의 게임번호와 선택 영역을 더 크게 촬영해 주세요.</div>}
      {!!matches.length && <div className="receipt-ocr-results">
        {matches.map((row) => <label key={row.key}>
          <input type="checkbox" checked={row.selected} onChange={(event) => update(row.key, { selected: event.target.checked })} />
          <span><b>{row.game.home} vs {row.game.away}</b><small>{row.option["게임번호"]}번 · {row.option.market} {row.option.label || ""} · {row.option["선택"]}</small></span>
          <input aria-label="구매 배당" type="number" min="1.01" step="0.01" value={row.purchaseOdds} onChange={(event) => update(row.key, { purchaseOdds: event.target.value })} />
          <input aria-label="투입금" type="number" min="100" step="100" value={row.stake} onChange={(event) => update(row.key, { stake: event.target.value })} />
        </label>)}
        <button type="button" disabled={!matches.some((row) => row.selected)} onClick={importSelected}>선택한 항목을 내 베팅에 저장</button>
      </div>}
      <small className="receipt-ocr-privacy">사진은 이 브라우저에서 문자 인식하며 서버에 저장하지 않습니다. 최초 실행 시 한국어 OCR 모델을 내려받습니다.</small>
    </section>
  );
}
