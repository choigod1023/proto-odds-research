import { useEffect, useRef, useState } from "react";
import {
  createTicketRecords,
  readBetLedger,
  writeBetLedger,
} from "../lib/bet-ledger.js";
import {
  scanReceiptImage,
  decodeBrowserImage,
  encodeBrowserImage,
} from "../lib/receipt-scan.js";
import {
  receiptDrafts,
  linkReceiptDraft,
  scannedTicketSummary,
  receiptSaveIssue,
  receiptRecordRows,
  RECEIPT_CHOICES,
} from "../lib/receipt-draft.js";
import ReceiptImageInput from "./ReceiptImageInput.jsx";

export function ReceiptLineInput({ label, value, onChange }) {
  // HTML number inputs silently erase a leading '+'. Keep the printed sign,
  // and allow both signs on phone keyboards; receiptSaveIssue validates decimals.
  return (
    <input
      aria-label={label}
      type="text"
      inputMode="text"
      placeholder="예: +1.0, -1.0, 2.5"
      value={value}
      onChange={onChange}
    />
  );
}

export default function ReceiptOcr({ games = [], onImported }) {
  const [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(0);
  const [matches, setMatches] = useState([]),
    [ticket, setTicket] = useState({});
  const [error, setError] = useState(""),
    [text, setText] = useState(""),
    [preview, setPreview] = useState("");
  const [status, setStatus] = useState("");
  const job = useRef(0),
    activeWorker = useRef(null),
    locked = useRef(false);
  useEffect(
    () => () => {
      job.current++;
      const worker = activeWorker.current;
      activeWorker.current = null;
      void worker?.terminate();
    },
    [],
  );
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  const scan = async (file) => {
    if (!file || locked.current) return;
    locked.current = true;
    const id = ++job.current;
    setBusy(true);
    setProgress(0);
    setError("");
    setMatches([]);
    setText("");
    setTicket({});
    setStatus("");
    setPreview(URL.createObjectURL(file));
    let worker;
    try {
      const { createWorker } = await import("tesseract.js");
      worker = await createWorker("kor+eng", 1);
      if (id !== job.current) {
        await worker.terminate();
        return;
      }
      activeWorker.current = worker;
      const result = await scanReceiptImage(file, {
        worker,
        decode: decodeBrowserImage,
        encode: encodeBrowserImage,
        onProgress: (value) => {
          if (id === job.current) setProgress(value);
        },
      });
      if (id !== job.current) return;
      setText(result.text);
      setMatches(receiptDrafts(result));
      setTicket(scannedTicketSummary(result));
      setProgress(100);
      if (!result.rows.length)
        setError(
          "선택된 색상 박스를 찾지 못했습니다. 한 투표지의 경기·선택·하단 금액이 함께 보이도록 잘라 넣어 주세요. 흑백·흐릿한 사진은 자동 선택을 추측하지 않습니다.",
        );
    } catch (cause) {
      if (id === job.current)
        setError(`사진을 읽지 못했습니다: ${cause?.message || cause}`);
    } finally {
      if (activeWorker.current === worker) {
        activeWorker.current = null;
        await worker?.terminate().catch(() => {});
      }
      locked.current = false;
      if (id === job.current) setBusy(false);
    }
  };
  const update = (key, patch) =>
    setMatches((current) =>
      current.map((row) =>
        row.key === key
          ? {
              ...row,
              ...patch,
              ...("reviewed" in patch ? {} : { reviewed: false }),
              ...([
                "home",
                "away",
                "year",
                "md",
                "gameNo",
                "market",
                "line",
                "choice",
              ].some((field) => field in patch) && !("game" in patch)
                ? {
                    game: null,
                    option: null,
                    matchStatus: "수정한 인식값 · 원본 확인 필요",
                  }
                : {}),
            }
          : row,
      ),
    );
  const updateTicket = (patch) =>
    setTicket((current) => ({
      ...current,
      ...patch,
      ...("reviewed" in patch ? {} : { reviewed: false }),
    }));
  const issue = receiptSaveIssue(matches, ticket);
  const save = () => {
    if (busy || receiptSaveIssue(matches, ticket)) return;
    try {
      const records = createTicketRecords(receiptRecordRows(matches), ticket);
      writeBetLedger([...records, ...readBetLedger()]);
      setStatus(
        `${records.length}경기를 이 기기에 저장했습니다. 원본 이미지와 인식 내용은 서버에 전송하지 않았습니다.`,
      );
      setMatches([]);
      setText("");
      setPreview("");
      onImported?.(records.length);
    } catch {
      setError(
        "저장 공간이 부족하거나 브라우저 저장이 차단되었습니다. 인식 결과는 유지했습니다。",
      );
    }
  };
  return (
    <section className="receipt-ocr" aria-busy={busy}>
      <div className="receipt-ocr-head">
        <div>
          <small>사진 자동 입력</small>
          <h2>프로토 영수증 OCR</h2>
        </div>
      </div>
      <ReceiptImageInput onImage={scan} disabled={busy} />
      <p>
        문자 인식은 이 기기에서 실행합니다. 선택 박스·배당을 읽고 원본과 대조한
        뒤 저장하세요. 사진에 없는 연도나 확률은 추측하지 않습니다.
      </p>
      {preview && (
        <details open>
          <summary>원본 이미지 확인</summary>
          <img
            src={preview}
            alt="OCR 원본 투표지"
            style={{
              maxWidth: "100%",
              height: "auto",
              maxHeight: 480,
              objectFit: "contain",
            }}
          />
        </details>
      )}
      {busy && (
        <div className="receipt-ocr-progress" role="status">
          <i style={{ width: `${progress}%` }} />
          <span>
            이미지 인식 {progress}% — 처음에는 언어 파일을 준비합니다.
          </span>
        </div>
      )}
      {error && (
        <div className="receipt-ocr-error" role="alert">
          {error}
        </div>
      )}
      {status && <p role="status">{status}</p>}
      {!!matches.length && (
        <div className="receipt-draft-results">
          <p>
            {matches.length}개 선택 박스 인식 · 사진의 선택경기수{" "}
            {ticket.legCount ?? "확인 필요"}개. 빈 항목·오독은 아래에서 수정할
            수 있습니다.
          </p>
          {matches.map((row, index) => (
            <fieldset key={row.key} className="receipt-draft-row">
              <legend>
                {index + 1}번째 선택 · {row.gameNo || "번호 확인"}번 ·{" "}
                {row.choice || "선택 확인"} {row.purchaseOdds}배
              </legend>
              <label>
                <input
                  type="checkbox"
                  checked={row.selected}
                  onChange={(event) =>
                    update(row.key, { selected: event.target.checked })
                  }
                />{" "}
                이 경기 저장
              </label>
              <div className="receipt-draft-fields">
                {[
                  ["gameNo", "경기번호"],
                  ["year", "경기 연도"],
                  ["md", "경기 월.일"],
                  ["home", "홈팀"],
                  ["away", "원정팀"],
                ].map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <input
                      aria-label={`${index + 1}번 ${label}`}
                      value={row[key]}
                      onChange={(event) =>
                        update(row.key, { [key]: event.target.value })
                      }
                    />
                  </label>
                ))}
                <label>
                  마켓
                  <select
                    aria-label={`${index + 1}번 마켓`}
                    value={row.market}
                    onChange={(event) =>
                      update(row.key, { market: event.target.value })
                    }
                  >
                    <option value="">확인 필요</option>
                    {["승패", "승무패", "핸디캡", "언더오버"].map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                </label>
                <label>
                  기준점
                  <ReceiptLineInput
                    label={`${index + 1}번 기준점`}
                    value={row.line}
                    onChange={(event) =>
                      update(row.key, { line: event.target.value })
                    }
                  />
                </label>
                <label>
                  선택
                  <select
                    aria-label={`${index + 1}번 선택`}
                    value={row.choice}
                    onChange={(event) =>
                      update(row.key, { choice: event.target.value })
                    }
                  >
                    <option value="">확인 필요</option>
                    {RECEIPT_CHOICES.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                </label>
                <label>
                  구매 배당
                  <input
                    aria-label={`${index + 1}번 구매 배당`}
                    type="number"
                    min="1.01"
                    step="0.01"
                    value={row.purchaseOdds}
                    onChange={(event) =>
                      update(row.key, { purchaseOdds: event.target.value })
                    }
                  />
                </label>
              </div>
              <button
                type="button"
                onClick={() =>
                  update(row.key, {
                    ...linkReceiptDraft(row, games),
                    reviewed: false,
                  })
                }
              >
                날짜·팀·번호로 경기 연결 확인
              </button>
              <small>{row.matchStatus}</small>
              <details>
                <summary>이 영역의 인식 원문</summary>
                <pre
                  style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {row.teamText}
                  {"\n선택 박스: "}
                  {row.buttonText}
                  {"\n"}
                  {row.sourceText}
                </pre>
              </details>
              <label>
                <input
                  type="checkbox"
                  checked={row.reviewed}
                  onChange={(event) =>
                    update(row.key, { reviewed: event.target.checked })
                  }
                />{" "}
                원본과 경기·선택·배당이 일치함을 확인했습니다.
              </label>
            </fieldset>
          ))}
          <div className="receipt-draft-fields">
            {[
              ["combinedOdds", "조합배당", "number"],
              ["stake", "총 투입금", "number"],
              ["expectedPayout", "예상적중금", "number"],
              ["purchasedAt", "구매일 (연도 포함)", "date"],
            ].map(([key, label, type]) => (
              <label key={key}>
                {label}
                <input
                  aria-label={label}
                  type={type}
                  step={key === "combinedOdds" ? "0.01" : undefined}
                  value={ticket[key] ?? ""}
                  onChange={(event) =>
                    updateTicket({ [key]: event.target.value })
                  }
                />
              </label>
            ))}
          </div>
          <label>
            <input
              type="checkbox"
              checked={Boolean(ticket.reviewed)}
              onChange={(event) =>
                updateTicket({ reviewed: event.target.checked })
              }
            />{" "}
            사진의 조합배당·투입금·예상적중금·구매일을 확인했습니다.
          </label>
          <p role="status">
            {issue || "확인한 내용을 이 기기에 저장할 수 있습니다."}
          </p>
          <button
            type="button"
            disabled={busy || Boolean(issue)}
            onClick={save}
          >
            확인한 투표지 저장
          </button>
        </div>
      )}
      {text && (
        <details>
          <summary>전체 인식 원문</summary>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {text}
          </pre>
        </details>
      )}
    </section>
  );
}
