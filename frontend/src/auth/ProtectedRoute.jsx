import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function AuthLoadingScreen() {
  return (
    <div className="auth-loading-screen" role="status" aria-live="polite">
      <div className="auth-loading-mark">NF</div>
      <div>
        <strong>Loading secure workspace</strong>
        <span>Checking your session...</span>
      </div>
    </div>
  );
}

export default function ProtectedRoute({ children, roles, allowPasswordSetup = false }) {
  const location = useLocation();
  const { user, initializing } = useAuth();

  if (initializing) return <AuthLoadingScreen />;

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (user.must_change_password && !allowPasswordSetup) {
    return <Navigate to="/set-password" replace />;
  }

  if (!user.must_change_password && location.pathname === "/set-password") {
    return <Navigate to="/live-monitor" replace />;
  }

  if (roles && !roles.includes(user.role)) {
    return <Navigate to="/access-denied" replace />;
  }

  return children;
}
