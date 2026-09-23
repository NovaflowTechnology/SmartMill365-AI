import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import api, { clearAccessToken, refreshAccessToken, setAccessToken } from "../api";

const AuthContext = createContext(null);

export function authErrorMessage(error, fallback = "The request could not be completed.") {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") {
    return detail.message;
  }
  const topMessage = error?.response?.data?.message;
  if (typeof topMessage === "string" && topMessage.trim()) return topMessage;
  return fallback;
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [initializing, setInitializing] = useState(true);
  const [sessionNotice, setSessionNotice] = useState("");

  const applySession = useCallback((payload) => {
    setAccessToken(payload?.access_token || null);
    setUser(payload?.user || null);
    setSessionNotice("");
  }, []);

  const clearSession = useCallback((notice = "") => {
    clearAccessToken();
    setUser(null);
    setSessionNotice(notice);
  }, []);

  const refreshProfile = useCallback(async () => {
    const response = await api.get("/api/auth/me");
    setUser(response.data?.user || null);
    return response.data?.user || null;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await refreshAccessToken({ silent: true });
        if (!cancelled && payload?.user) {
          setUser(payload.user);
        }
      } catch {
        if (!cancelled) {
          clearAccessToken();
          setUser(null);
        }
      } finally {
        if (!cancelled) setInitializing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleForcedLogout = (event) => {
      clearSession(event?.detail?.message || "Your session ended. Please sign in again.");
    };
    const handleProfileRefresh = () => {
      refreshProfile().catch(() => {});
    };
    window.addEventListener("auth:forced-logout", handleForcedLogout);
    window.addEventListener("auth:refresh-profile", handleProfileRefresh);
    return () => {
      window.removeEventListener("auth:forced-logout", handleForcedLogout);
      window.removeEventListener("auth:refresh-profile", handleProfileRefresh);
    };
  }, [clearSession, refreshProfile]);

  const login = useCallback(async (email, password) => {
    const response = await api.post("/api/auth/login", { email, password });
    applySession(response.data);
    return response.data;
  }, [applySession]);

  const setPrivatePassword = useCallback(async (newPassword) => {
    const response = await api.post("/api/auth/set-password", { new_password: newPassword });
    applySession(response.data);
    return response.data;
  }, [applySession]);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } catch {
      // Local session is still cleared even if the server session already expired.
    } finally {
      clearSession();
    }
  }, [clearSession]);

  const value = useMemo(() => ({
    user,
    initializing,
    isAuthenticated: Boolean(user),
    sessionNotice,
    login,
    logout,
    setPrivatePassword,
    refreshProfile,
    clearSession,
  }), [user, initializing, sessionNotice, login, logout, setPrivatePassword, refreshProfile, clearSession]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider.");
  return value;
}
