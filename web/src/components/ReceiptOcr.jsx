import { useState } from "react";
import { createTicketRecords, upsertBet } from "../lib/bet-ledger.js";
import { receiptRows, receiptTicketSummary } from "../lib/receipt-ocr.js";
import { buttonOdds, selectedButtonRects, visualChoiceIndex } from "../lib/receipt-image.js";
import { submitAnonymousTicket } from "../lib/anonymous-bets.js";

export default function ReceiptOcr({ games = [], onImported }) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [text, setText] = useState("");
  const [matches, setMatches] = useState([]);
  const [ticket, setTicket] = useState({ stake: 10000, combinedOdds: 0, expectedPayout: 0 });
  const [error, setError] = useState("");
  const [shareStatus, setShareStatus] = useState("");

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
      const [result, visual] = await Promise.all([
        worker.recognize(file, { rotateAuto: true }), selectedButtonRects(file).catch(() => ({ rects: [], width: 0 })),
      ]);
      const recognized = result?.data?.text || "";
      setText(recognized);
      const baseRows = receiptRows(recognized, games);
      const visualRects = visual.rects.length === baseRows.length ? visual.rects : [];
      const rows = [];
      for (let index = 0; index < baseRows.length; index += 1) {
        const row = baseRows[index]; const rect = visualRects[index];
        if (!row.needsConfirmation || !rect) { rows.push({ ...row, selected: true }); continue; }
        const choiceIndex = visualChoiceIndex(rect, row.optionChoices.length, visual.width);
        const option = row.optionChoices[choiceIndex];
        let purchaseOdds = "";
        try {
          const crop = await worker.recognize(file, { rectangle: rect });
          purchaseOdds = buttonOdds(crop?.data?.text) || "";
        } catch { /* 사용자가 확인할 수 있도록 빈 배당으로 유지 */ }
        rows.push({ ...row, option, purchaseOdds, selected: true, needsConfirmation: !option, visualDetected: true });
      }
      setMatches(rows); setTicket(receiptTicketSummary(recognized, rows));
    } catch (cause) {
      setError(`사진을 읽지 못했습니다: ${cause?.message || cause}`);
    } finally {
      await worker?.terminate?.();
      setBusy(false);
    }
  };

  const importSelected = () => {
    const selected = matches.filter((row) => row.selected && row.option);
    createTicketRecords(selected, ticket).forEach((record) => upsertBet(record));
    setShareStatus("전송 중");
    submitAnonymousTicket(selected, ticket).then((ok) => setShareStatus(ok ? "익명 통계 반영됨" : "익명 통계 전송 실패"))
      .catch(() => setShareStatus("익명 통계 전송 실패"));
    onImported?.(selected.length);
    setMatches([]); setText("");
  };

  const update = (key, patch) => setMatches((current) => current.map((row) => row.key === key ? { ...row, ...patch } : row));
  const confirmed = matches.filter((row) => row.selected && row.option);
  return (
    <section className="receipt-ocr">
      <div className="receipt-ocr-head">
        <div><small>사진 자동 입력</small><h2>프로토 영수증 OCR</h2></div>
        <label className={busy ? "is-disabled" : ""}>사진 선택·촬영
          <input type="file" accept="image/*" capture="environment" disabled={busy}
            onChange={(event) => scan(event.target.files?.[0])} />
        </label>
      </div>
      <p>한 사진의 선택 경기를 하나의 티켓으로 묶습니다. 조합배당·공통 투입금·예상적중금을 확인한 뒤 저장하세요.</p>
      {busy && <div className="receipt-ocr-progress"><i style={{ width: `${progress}%` }} /><span>문자 인식 {progress}%</span></div>}
      {error && <div className="receipt-ocr-error">{error}</div>}
      {!busy && text && !matches.length && <div className="receipt-ocr-error">일치하는 발매 선택지를 찾지 못했습니다. 사진의 게임번호와 선택 영역을 더 크게 촬영해 주세요.</div>}
      {!!matches.length && <div className="receipt-ocr-results">
        {matches.map((row) => <label key={row.key}>
          <input type="checkbox" checked={row.selected} onChange={(event) => update(row.key, { selected: event.target.checked })} />
          <span><b>{row.game.home} vs {row.game.away}</b><small>{row.option?.["게임번호"] || row.sourceText}번 · {row.option ? `${row.option.market} ${row.option.label || ""} · ${row.option["선택"]}` : "번호 인식됨 · 선택 확인 필요"}</small></span>
          {row.needsConfirmation && <select aria-label="선택 확인" value={row.option ? row.optionChoices.indexOf(row.option) : ""}
            onChange={(event) => {
              const option = event.target.value === "" ? null : row.optionChoices[Number(event.target.value)];
              update(row.key, { option, purchaseOdds: option?.["배당"] || "", needsConfirmation: !option });
            }}><option value="">픽 선택</option>{row.optionChoices.map((option, index) => <option key={`${option.market}-${option["선택"]}`} value={index}>{option.market} {option.label || ""} · {option["선택"]}</option>)}</select>}
          <input aria-label="구매 배당" type="number" min="1.01" step="0.01" value={row.purchaseOdds} placeholder="구매 배당" onChange={(event) => update(row.key, { purchaseOdds: event.target.value })} />
          <span className="receipt-leg-odds">{row.purchaseOdds ? `개별 ${Number(row.purchaseOdds).toFixed(2)}배` : "배당 확인"}</span>
        </label>)}
        <div className="receipt-ticket-fields">
          <label>조합배당<input aria-label="조합배당" type="number" min="1.01" step="0.01" value={ticket.combinedOdds} onChange={(event) => setTicket({ ...ticket, combinedOdds: event.target.value })} /></label>
          <label>총 투입금<input aria-label="총 투입금" type="number" min="100" step="100" value={ticket.stake} onChange={(event) => setTicket({ ...ticket, stake: event.target.value })} /></label>
          <label>예상적중금<input aria-label="예상적중금" type="number" min="0" step="100" value={ticket.expectedPayout} onChange={(event) => setTicket({ ...ticket, expectedPayout: event.target.value })} /></label>
        </div>
        <button type="button" disabled={!confirmed.length || matches.some((row) => row.selected && (!row.option || !Number(row.purchaseOdds)))} onClick={importSelected}>{confirmed.length > 1 ? `${confirmed.length}폴더 조합으로 저장` : "단폴로 저장"}</button>
      </div>}
      <small className="receipt-ocr-privacy">사진은 이 브라우저에서 문자 인식하며 서버에 저장하지 않습니다. 최초 실행 시 한국어 OCR 모델을 내려받습니다.</small>
      <small className="receipt-ocr-privacy">저장 시 경기번호·픽·배당·금액구간·폴더 수만 익명 통계로 전송합니다. 이미지·구매번호·IP·쿠키는 통계에 저장하지 않습니다.{shareStatus ? ` · ${shareStatus}` : ""}</small>
    </section>
  );
}
