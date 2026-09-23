import { useState } from "react";
import { authErrorMessage, useAuth } from "../auth/AuthContext";

export default function SetPasswordPage() {
  const { user, setPrivatePassword } = useAuth();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (password.length < 6) {
      setError("Password must contain at least 6 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The password confirmation does not match.");
      return;
    }
    setSaving(true);
    try {
      await setPrivatePassword(password);
      window.location.replace("/live-monitor");
    } catch (err) {
      setError(authErrorMessage(err, "The password could not be updated."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="password-gate-page">
      <section className="password-gate-card">
        <div className="password-gate-mark">NF</div>
        <div className="password-gate-heading">
          <span className="auth-eyebrow auth-eyebrow--dark">First-time security setup</span>
          <h1>Set your private password</h1>
          <p>You are signed in with a temporary password. Create a private password before accessing the system.</p>
        </div>
        <div className="password-gate-user"><span>Signed in as</span><strong>{user?.full_name}</strong><small>{user?.email}</small></div>
        {error ? <div className="auth-alert auth-alert--error" role="alert">{error}</div> : null}
        <form className="auth-form" onSubmit={submit}>
          <label><span>New Password</span><input type={show ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" /></label>
          <label><span>Confirm Password</span><input type={show ? "text" : "password"} value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="new-password" /></label>
          <label className="password-visibility-toggle"><input type="checkbox" checked={show} onChange={(event) => setShow(event.target.checked)} /><span>Show passwords</span></label>
          <div className="password-requirement"><span className={password.length >= 6 ? "met" : ""}>✓</span> At least 6 characters</div>
          <button type="submit" className="auth-submit-button" disabled={saving}>{saving ? "Updating password..." : "Set Password and Continue"}</button>
        </form>
        <p className="password-gate-note">After setup, password changes are handled by an administrator.</p>
      </section>
    </main>
  );
}
