import { useEffect, useId, useRef, useState } from "react";
import {
  CLIPBOARD_FALLBACK, RECEIPT_IMAGE_ACCEPT, readReceiptClipboard,
  receiptFilesFromTransfer, validateReceiptImage,
} from "../lib/receipt-input.js";

/** Local file acquisition only. onImage may return a promise; OCR stays with the caller. */
export default function ReceiptImageInput({ onImage, disabled = false }) {
  const id = useId();
  const album = useRef(null);
  const camera = useRef(null);
  const mounted = useRef(true);
  const locked = useRef(false);
  const current = useRef({ onImage, disabled });
  current.current = { onImage, disabled };
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState("");
  const unavailable = disabled || pending;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const report = (message) => { if (mounted.current) setStatus(message); };
  const deliver = async (files) => {
    if (!mounted.current || current.current.disabled) return;
    if (files.length !== 1) {
      report(files.length ? "이미지는 한 번에 한 장씩 선택해 주세요." : "이미지 한 장을 선택해 주세요.");
      return;
    }
    const file = files[0];
    const validation = validateReceiptImage(file);
    if (!validation.ok) { report(validation.message); return; }
    report(`${file.name || "이미지"} 선택됨. 이미지 처리 중입니다.`);
    try {
      await current.current.onImage(file);
      report(`${file.name || "이미지"} 전달 완료.`);
    } catch {
      report("이미지 처리 중 오류가 발생했습니다. 다른 사진을 선택하거나 다시 시도해 주세요.");
    }
  };

  const acquire = async (getFiles) => {
    if (current.current.disabled || locked.current) return;
    locked.current = true;
    setPending(true);
    try {
      // Invoke before the first await to preserve clipboard user activation.
      const files = await getFiles();
      await deliver(files);
    } catch (error) {
      report(error?.message || CLIPBOARD_FALLBACK);
    } finally {
      locked.current = false;
      if (mounted.current) setPending(false);
    }
  };

  const changeFile = (event) => {
    const files = Array.from(event.currentTarget.files || []);
    event.currentTarget.value = ""; // A second selection of the same file must fire change.
    if (files.length) void acquire(() => files); // Cancelling the picker keeps the status.
  };

  const paste = (event) => {
    const files = receiptFilesFromTransfer(event.clipboardData);
    if (files.length) event.preventDefault();
    if (unavailable) return;
    if (!files.length) { report(`붙여넣은 내용에 이미지가 없습니다. ${CLIPBOARD_FALLBACK}`); return; }
    void acquire(() => files);
  };

  return (
    <div className="receipt-image-input" role="group" aria-labelledby={`${id}-label`}
      aria-disabled={unavailable} style={{ minWidth: 0 }}>
      <p id={`${id}-label`} className="receipt-image-input-label">영수증·베트맨 화면 이미지 선택</p>
      <div className="receipt-image-input-dropzone" role="group" tabIndex={unavailable ? -1 : 0}
        aria-label="이미지 붙여넣기 및 끌어놓기 영역" aria-disabled={unavailable}
        aria-describedby={`${id}-help ${id}-status`} onPaste={paste}
        onDragOver={(event) => {
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = unavailable ? "none" : "copy";
        }}
        onDrop={(event) => {
          event.preventDefault();
          const files = receiptFilesFromTransfer(event.dataTransfer);
          if (!unavailable) void acquire(() => files);
        }}
        style={{ border: "1px dashed currentColor", borderRadius: 8, padding: 16 }}>
        이 영역을 선택한 뒤 Ctrl+V / ⌘V로 붙여넣거나 이미지를 끌어놓으세요.
      </div>
      <div className="receipt-image-input-actions" style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
        <button type="button" disabled={unavailable} onClick={() => album.current?.click()}>사진 선택 (앨범·파일)</button>
        <button type="button" disabled={unavailable} onClick={() => camera.current?.click()}>카메라로 촬영</button>
        <button type="button" disabled={unavailable}
          onClick={() => void acquire(() => readReceiptClipboard(globalThis.navigator?.clipboard).then((file) => [file]))}>
          클립보드 이미지 붙여넣기
        </button>
      </div>
      <input ref={album} className="receipt-image-input-album" type="file" hidden
        aria-label="앨범 또는 파일에서 이미지 선택" accept={RECEIPT_IMAGE_ACCEPT} disabled={unavailable} onChange={changeFile} />
      <input ref={camera} className="receipt-image-input-camera" type="file" hidden
        aria-label="후면 카메라로 이미지 촬영" accept="image/*" capture="environment" disabled={unavailable} onChange={changeFile} />
      <p id={`${id}-help`} className="receipt-image-input-help">
        한 번에 한 장, 최대 20 MiB. PNG·JPEG·WebP·GIF·BMP·AVIF 지원. 이 입력 영역은 이미지를 서버에 전송하지 않습니다.
      </p>
      <p id={`${id}-status`} className="receipt-image-input-status" role="status" aria-live="polite" aria-atomic="true"
        style={{ overflowWrap: "anywhere" }}>
        {disabled ? "현재 이미지 입력을 사용할 수 없습니다. 처리가 끝난 뒤 다시 시도해 주세요." : status || "이미지를 선택해 주세요."}
      </p>
    </div>
  );
}
