import { useEffect } from "react";

function ModalIcon({ tone }) {
  const stroke = "currentColor";
  if (tone === "danger" || tone === "warning") {
    return (
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M10.3 3.6 2.5 17a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z" />
        <path d="M12 9v4M12 17h.01" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" />
    </svg>
  );
}

export default function ProfessionalModal({
  open,
  title,
  subtitle,
  tone = "info",
  children,
  actions,
  onClose,
  closeOnBackdrop = true,
  labelledBy = "professional-modal-title",
}) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape" && onClose) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="pro-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (closeOnBackdrop && event.target === event.currentTarget && onClose) onClose();
      }}
    >
      <section className="pro-modal" role="dialog" aria-modal="true" aria-labelledby={labelledBy}>
        <header className="pro-modal-header">
          <span className={`pro-modal-icon pro-modal-icon--${tone}`}><ModalIcon tone={tone} /></span>
          <div>
            <h2 id={labelledBy}>{title}</h2>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          {onClose ? (
            <button type="button" className="pro-modal-close" onClick={onClose} aria-label="Close dialog">
              <svg
                viewBox="0 0 24 24"
                width="18"
                height="18"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M6 6 18 18M18 6 6 18" />
              </svg>
            </button>
          ) : null}
        </header>
        <div className="pro-modal-body">{children}</div>
        {actions ? <footer className="pro-modal-actions">{actions}</footer> : null}
      </section>
    </div>
  );
}
