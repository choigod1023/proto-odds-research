import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

export default function GameInfoModal({ title, onClose, children }) {
  const dialog = useRef(null);
  const heading = useId();
  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement;
    const overflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);
  return createPortal(
    <dialog ref={dialog} className="game-info-modal" aria-labelledby={heading}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right ||
            event.clientY < rect.top || event.clientY > rect.bottom) onClose();
      }}>
      <header className="game-info-modal-header">
        <h2 id={heading}>{title} · 경기정보</h2>
        <button type="button" autoFocus onClick={onClose} aria-label="경기정보 닫기">닫기 ✕</button>
      </header>
      <div className="game-info-modal-body">{children}</div>
    </dialog>, document.body,
  );
}
