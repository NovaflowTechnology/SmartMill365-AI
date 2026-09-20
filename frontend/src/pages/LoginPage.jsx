import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import NotificationToast from "../components/NotificationToast";
import { authErrorMessage, useAuth } from "../auth/AuthContext";

function EyeIcon({ visible }) {
  // Keep the same eye geometry in both states so the icon never shifts.
  // When the password is visible, only the slash is added.
  return (
    <svg
      className="auth-password-eye-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2 12s3.7-7 10-7 10 7 10 7-3.7 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
      {visible ? <path d="M4 4l16 16" /> : null}
    </svg>
  );
}

export default function LoginPage() {
  const { user, initializing, login, sessionNotice } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (sessionNotice) setError(sessionNotice);
  }, [sessionNotice]);

  if (!initializing && user) {
    return <Navigate to={user.must_change_password ? "/set-password" : "/live-monitor"} replace />;
  }

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (!email.trim() || !password) {
      setError("Enter your email and password to continue.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await login(email.trim(), password);
      if (result.user?.must_change_password) {
        navigate("/set-password", { replace: true });
      } else {
        const requested = location.state?.from;
        navigate(requested && requested !== "/login" ? requested : "/live-monitor", { replace: true });
      }
    } catch (err) {
      setError(authErrorMessage(err, "Invalid email or password."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <section className="auth-brand-panel">
        <div className="auth-brand-top">
          <div className="auth-brand-logo">NF</div>
          <div><strong>Sterilizer Intelligence</strong><span>Industrial Analytics Platform</span></div>
        </div>
        <div className="auth-brand-content">
          <span className="auth-eyebrow">Operational Intelligence</span>
          <h1>AI-assisted sterilization monitoring and analysis.</h1>
          <p>Secure access to live monitoring, cycle comparison, AI analysis, and daily operational reporting.</p>
          <div className="auth-capability-grid">
            <div><strong>Live</strong><span>Pressure monitoring</span></div>
            <div><strong>Analyze</strong><span>Cycle-to-benchmark scoring</span></div>
            <div><strong>AI</strong><span>Evidence-backed insights</span></div>
            <div><strong>Report</strong><span>Shift and daily summaries</span></div>
          </div>
        </div>
        <div className="auth-brand-footer">Authorized personnel only</div>
      </section>

      <section className="auth-form-panel">
        <div className="auth-form-card">
          <div className="auth-form-heading">
            <span className="auth-eyebrow auth-eyebrow--dark">Secure Workspace</span>
            <h2>Welcome back</h2>
            <p>Sign in with your assigned account to continue.</p>
          </div>

          <form onSubmit={submit} className="auth-form">
            <label>
              <span>Email</span>
              <input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@company.com" disabled={submitting} />
            </label>
            <label>
              <span>Password</span>
              <div className="auth-password-input">
                <input type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" disabled={submitting} />
                <button
                  type="button"
                  className="auth-password-toggle"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  onMouseDown={(event) => event.preventDefault()}
                  disabled={submitting}
                >
                  <EyeIcon visible={showPassword} />
                </button>
              </div>
            </label>
            <button type="submit" className="auth-submit-button" disabled={submitting}>{submitting ? "Signing in..." : "Sign In"}</button>
          </form>

          <div className="auth-help-box">
            <p><strong>Don't have an account?</strong> Contact your system administrator.</p>
            <p><strong>Forgot your password?</strong> Contact your system administrator for a temporary password.</p>
          </div>
        </div>
      </section>

      {error ? (
        <NotificationToast
          tone="error"
          message={error}
          autoDismissMs={8000}
          onClose={() => setError("")}
        />
      ) : null}
    </main>
  );
}
