import { useState } from "react";
import ProfessionalModal from "./ProfessionalModal";

function EyeIcon({ hidden }) {
  return hidden ? (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m3 3 18 18"/><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"/><path d="M9.9 4.2A10.7 10.7 0 0 1 12 4c5 0 9 4.6 10 8a12 12 0 0 1-2 3.8"/><path d="M6.6 6.6C4.3 8 2.8 10.1 2 12c1 3.4 5 8 10 8 1.5 0 2.9-.4 4.2-1"/></svg>
  ) : (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>
  );
}

function CopyIcon() {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"/></svg>;
}

export default function OneTimePasswordModal({ open, account, password, title = "Temporary Password Generated", onDone }) {
  const [visible, setVisible] = useState(false);
  const [copied, setCopied] = useState(false);

  const copyPassword = async () => {
    try {
      await navigator.clipboard.writeText(password || "");
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <ProfessionalModal
      open={open}
      title={title}
      subtitle="Provide these credentials to the user through your approved manual communication channel."
      tone="info"
      closeOnBackdrop={false}
      onClose={null}
      actions={<button type="button" className="btn-primary" onClick={onDone}>Done</button>}
      labelledBy="one-time-password-title"
    >
      <div className="otp-account-summary">
        <div><span>User</span><strong>{account?.full_name || "-"}</strong></div>
        <div><span>Email</span><strong>{account?.email || "-"}</strong></div>
      </div>
      <div className="otp-field-block">
        <label>Temporary Password</label>
        <div className="otp-password-field">
          <code>{visible ? password : "•".repeat(Math.max(12, password?.length || 16))}</code>
          <button type="button" className="icon-action-button" onClick={() => setVisible((value) => !value)} aria-label={visible ? "Hide temporary password" : "Show temporary password"} title={visible ? "Hide password" : "Show password"}><EyeIcon hidden={visible} /></button>
          <button type="button" className="icon-action-button" onClick={copyPassword} aria-label="Copy temporary password" title="Copy password"><CopyIcon /></button>
        </div>
        <div className="otp-copy-status" aria-live="polite">{copied ? "Copied to clipboard" : "This password is shown only once and cannot be retrieved after this dialog is closed."}</div>
      </div>
      <div className="security-note-box">
        The user must sign in with this temporary password and create a private password before accessing the system.
      </div>
    </ProfessionalModal>
  );
}
