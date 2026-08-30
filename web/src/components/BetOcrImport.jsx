import { useMemo, useRef, useState } from "react";
import { createBetRecord, readBetLedger, upsertBet } from "../lib/bet-ledger.js";
import { betFingerprint, matchRecognizedRows, parseBetSlipText } from "../lib/bet-ocr.js";

const moneyValue = (value) => Math.max(0, Number(value) || 0);

export default function BetOcrImport({ oddsDocument, onSaved }) {
  const inputRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [drafts, setDrafts] = useState([]);
  const [message, setMessage] = useState("");
  const offers = useMemo(() => oddsDocument || { games: [] }, [oddsDocument]);

  const processFiles = async (files) => {
    if (!files?.length) return;
    setBusy(true); setMessage(""); setDrafts([]); setProgress(0);
    try {
      const { recognize } = await import("tesseract.js");
      const next = [];
      for (let index = 0; index < files.length; index += 1) {
        const result = await recognize(files[index], "kor+eng", {
          logger: (event) => {
            if (event.status === "recognizing text") setProgress((index + event.progress) / files.length);
          },
        });
        const parsed = parseBetSlipText(result.data.text, result.data.confidence);
        const matched = matchRecognizedRows(parsed, offers);
        matched.forEach((row) => next.push({
          ...row, round: parsed.round || "", purchasedAt: parsed.purchasedAt || "",
          stake: parsed.stake || 10000, fileName: files[index].name,
        }));
      }
      setDrafts(next);
      if (!next.length) setMessage("경기 행을 인식하지 못했습니다. 더 선명하게 촬영하거나 직접 베팅 기록을 이용하세요.");
    } catch (error) {
      setMessage(`OCR을 실행하지 못했습니다: ${error?.message || "알 수 없는 오류"}`);
    } finally {
      setBusy(false); setProgress(0);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const rematch = (draft, changes) => {
    const next = { ...draft, ...changes };
    const parsed = { round: next.round, rows: [next], confidence: next.confidence };
    const matched = matchRecognizedRows(parsed, offers)[0];
    return { ...next, ...matched, id: draft.id };
  };

  const save = () => {
    const existing = new Set(readBetLedger().map(betFingerprint));
    const duplicates = [];
    const saved = [];
    for (const draft of drafts) {
      if (!draft.match || !(draft.purchaseOdds > 1) || !(draft.stake > 0)) continue;
      const record = createBetRecord(draft.match.game, draft.match.option, {
        stake: draft.stake, purchaseOdds: draft.purchaseOdds,
        purchasedAt: draft.purchasedAt || undefined, source: "client_ocr_confirmed",
      });
      const fingerprint = betFingerprint(record);
      if (existing.has(fingerprint)) { duplicates.push(draft.gameNo); continue; }
      upsertBet(record); existing.add(fingerprint); saved.push(record);
    }
    setMessage(duplicates.length ? `${duplicates.join(", ")}번은 동일 베팅이 있어 제외했습니다.` : `${saved.length}건을 저장했습니다.`);
    if (saved.length) { setDrafts([]); onSaved?.(saved); }
  };

  return <section className="dashboard-ocr">
    <button type="button" className="is-primary" onClick={() => setOpen((value) => !value)}>베팅 이미지 불러오기</button>
    {open && <div className="dashboard-ocr-panel">
      <h2>이미지에서 베팅 가져오기</h2>
      <p>이미지는 이 브라우저에서만 OCR 처리하며 서버나 저장소에 업로드하지 않습니다. 확인 버튼을 누르기 전에는 원장에 저장되지 않습니다.</p>
      <input ref={inputRef} type="file" accept="image/*" capture="environment" multiple
        onChange={(event) => processFiles([...event.target.files])} />
      {busy && <p role="status">인식 중… {Math.round(progress * 100)}%</p>}
      {drafts.map((draft, index) => <article className="ocr-draft" key={draft.id}>
        <header><b>{draft.fileName}</b><span>OCR 신뢰도 {draft.confidence == null ? "–" : `${Math.round(draft.confidence * 100)}%`}</span></header>
        <div className="ocr-fields">
          <label>회차<input value={draft.round} onChange={(event) => setDrafts((rows) => rows.map((row, i) => i === index ? rematch(row, { round: event.target.value }) : row))} /></label>
          <label>게임번호<input value={draft.gameNo} onChange={(event) => setDrafts((rows) => rows.map((row, i) => i === index ? rematch(row, { gameNo: event.target.value }) : row))} /></label>
          <label>마켓<select value={draft.market} onChange={(event) => setDrafts((rows) => rows.map((row, i) => i === index ? rematch(row, { market: event.target.value }) : row))}>
            {["승패", "승무패", "핸디캡", "언더오버"].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>선택<input value={draft.choice} onChange={(event) => setDrafts((rows) => rows.map((row, i) => i === index ? rematch(row, { choice: event.target.value }) : row))} /></label>
          <label>구매 배당<input type="number" min="1.01" step="0.01" value={draft.purchaseOdds} onChange={(event) => setDrafts((rows) => rows.map((row, i) => i === index ? { ...row, purchaseOdds: Number(event.target.value) } : row))} /></label>
          <label>투입금<input type="number" min="100" step="100" value={draft.stake} onChange={(event) => setDrafts((rows) => rows.map((row, i) => i === index ? { ...row, stake: moneyValue(event.target.value) } : row))} /></label>
        </div>
        {draft.match ? <p className="ocr-match">매칭: {draft.match.game.home} vs {draft.match.game.away} · {draft.match.option.market} {draft.match.option["선택"]}</p>
          : <p className="ocr-warning">{draft.failureReason}</p>}
      </article>)}
      {!!drafts.length && <button type="button" className="is-primary" disabled={!drafts.some((draft) => draft.match)} onClick={save}>확인한 항목만 내 베팅에 저장</button>}
      {message && <p role="status">{message}</p>}
    </div>}
  </section>;
}
