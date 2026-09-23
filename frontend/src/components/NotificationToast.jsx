import { useEffect, useRef } from "react";

export default function NotificationToast({
  message,
  tone = "info",
  autoDismissMs = 3000,
  onClose,
}) {
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!message || !autoDismissMs) return undefined;

    const timer = window.setTimeout(() => {
      onCloseRef.current?.();
    }, autoDismissMs);

    return () => window.clearTimeout(timer);
  }, [message, autoDismissMs]);

  if (!message) return null;

  return (
    <div
      className={`notification-toast notification-toast-${tone}`}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
    >
      <span className="notification-toast-indicator" aria-hidden="true" />
      <span className="notification-toast-message">{message}</span>
      <button
        type="button"
        className="notification-toast-close"
        onClick={onClose}
        aria-label="Close notification"
      >
        ×
      </button>
    </div>
  );
}
