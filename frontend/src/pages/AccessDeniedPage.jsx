import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export default function AccessDeniedPage() {
  const { user } = useAuth();
  return (
    <main className="access-denied-page">
      <section className="access-denied-card">
        <div className="access-denied-code">403</div>
        <h1>Access restricted</h1>
        <p>Your <strong>{user?.role || "current"}</strong> role does not have permission to access this page.</p>
        <Link className="btn-primary access-denied-action" to="/live-monitor">Return to Live Monitoring</Link>
      </section>
    </main>
  );
}
