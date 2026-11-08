// A dialog over the page (E9, P21, P23). The native <dialog> keeps keyboard focus inside and closes on
// Escape; clicking outside it closes it too.
import { useEffect, useRef } from "react";

export default function Dialog({ title, intro, onClose, onSubmit, submitLabel, busy, error, children }) {
  const box = useRef(null);
  useEffect(() => {
    const d = box.current;
    d.showModal();
    const close = () => onClose();
    d.addEventListener("close", close);
    return () => d.removeEventListener("close", close);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const id = `dlg-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <dialog ref={box} className="modal" aria-labelledby={id}
            onClick={(e) => { if (e.target === box.current) box.current.close(); }}>
      <form className="modal-body" onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
        <div className="modal-head">
          <h2 id={id}>{title}</h2>
          <button type="button" className="btn-quiet" onClick={() => box.current.close()} aria-label="Close">✕</button>
        </div>
        {intro && <p className="muted modal-intro">{intro}</p>}
        {children}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="modal-foot">
          <button type="button" className="btn" onClick={() => box.current.close()}>Cancel</button>
          <button className="btn primary" type="submit" disabled={busy}>{busy ? "Saving…" : submitLabel}</button>
        </div>
      </form>
    </dialog>
  );
}
