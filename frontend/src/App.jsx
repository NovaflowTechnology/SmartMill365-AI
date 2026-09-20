import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import BenchmarkPage from "./pages/BenchmarkPage";
import RcaRuleManagementPage from "./pages/RcaRuleManagementPage";
import ComparisonPage from "./pages/ComparisonPage";
import LiveMonitoringPage from "./pages/LiveMonitoringPage";
import DailyReportPage from "./pages/DailyReportPage";
import SettingsPage from "./pages/SettingsPage";
import LoginPage from "./pages/LoginPage";
import SetPasswordPage from "./pages/SetPasswordPage";
import AccessDeniedPage from "./pages/AccessDeniedPage";
import AccountManagementPage from "./pages/AccountManagementPage";
import ChatbotWidget from "./components/ChatbotWidget";
import ProtectedRoute, { AuthLoadingScreen } from "./auth/ProtectedRoute";
import { AuthProvider, useAuth } from "./auth/AuthContext";

const ALL_ROLES = ["admin", "editor", "viewer"];
const EDIT_ROLES = ["admin", "editor"];

function RootRedirect() {
  const { user, initializing } = useAuth();

  if (initializing) {
    return <AuthLoadingScreen />;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (user.must_change_password) {
    return <Navigate to="/set-password" replace />;
  }

  return <Navigate to="/live-monitor" replace />;
}

function AuthenticatedChatbot() {
  const location = useLocation();
  const { user } = useAuth();
  if (!user || user.must_change_password) return null;
  if (["/login", "/set-password", "/access-denied"].includes(location.pathname)) return null;
  return <ChatbotWidget />;
}

function AppRoutes() {
  return (
    <>
      <Routes>
        <Route path="/" element={<RootRedirect />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/set-password"
          element={<ProtectedRoute roles={ALL_ROLES} allowPasswordSetup><SetPasswordPage /></ProtectedRoute>}
        />
        <Route
          path="/access-denied"
          element={<ProtectedRoute roles={ALL_ROLES}><AccessDeniedPage /></ProtectedRoute>}
        />
        <Route path="/live-monitor" element={<ProtectedRoute roles={ALL_ROLES}><LiveMonitoringPage /></ProtectedRoute>} />
        <Route path="/compare" element={<ProtectedRoute roles={ALL_ROLES}><ComparisonPage /></ProtectedRoute>} />
        <Route path="/daily-report" element={<ProtectedRoute roles={ALL_ROLES}><DailyReportPage /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute roles={EDIT_ROLES}><SettingsPage /></ProtectedRoute>} />
        <Route path="/benchmark" element={<ProtectedRoute roles={EDIT_ROLES}><BenchmarkPage /></ProtectedRoute>} />
        <Route path="/rca-rules" element={<ProtectedRoute roles={EDIT_ROLES}><RcaRuleManagementPage /></ProtectedRoute>} />
        <Route path="/account-management" element={<ProtectedRoute roles={["admin"]}><AccountManagementPage /></ProtectedRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <AuthenticatedChatbot />
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
