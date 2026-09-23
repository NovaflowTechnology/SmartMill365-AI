import axios from "axios";

const rawEnvBaseUrl = import.meta.env.VITE_API_BASE_URL;
const cleanEnvBaseUrl = rawEnvBaseUrl && rawEnvBaseUrl.trim() !== "" ? rawEnvBaseUrl.trim() : null;

const devApiHost = typeof window !== "undefined" && window.location.hostname === "127.0.0.1"
  ? "127.0.0.1"
  : "localhost";

const baseURL = cleanEnvBaseUrl
  ? cleanEnvBaseUrl
  : import.meta.env.DEV
    ? `http://${devApiHost}:8000`
    : "";

let accessToken = null;
let refreshPromise = null;

export function setAccessToken(token) {
  accessToken = token || null;
}

export function clearAccessToken() {
  accessToken = null;
}

export function getAccessToken() {
  return accessToken;
}

const api = axios.create({
  baseURL,
  timeout: 300000,
  withCredentials: true,
});

const rawAuthClient = axios.create({
  baseURL,
  timeout: 30000,
  withCredentials: true,
});

function dispatchAuthEvent(name, detail = {}) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }
}

function responseCode(error) {
  return error?.response?.data?.code || error?.response?.data?.detail?.code || "";
}

function responseMessage(error) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return error?.response?.data?.message || "";
}

export async function refreshAccessToken({ silent = false } = {}) {
  if (!refreshPromise) {
    refreshPromise = rawAuthClient
      .post("/api/auth/refresh", {})
      .then((response) => {
        setAccessToken(response.data?.access_token || null);
        return response.data;
      })
      .catch((error) => {
        clearAccessToken();
        if (!silent) {
          dispatchAuthEvent("auth:forced-logout", {
            message: responseMessage(error) || "Your session has expired. Please sign in again.",
          });
        }
        throw error;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const response = error?.response;
    const original = error?.config || {};
    const requestReference = response?.headers?.["x-request-id"];
    const detail = response?.data?.detail;
    const code = responseCode(error);
    const url = String(original?.url || "");

    if (
      requestReference &&
      typeof detail === "string" &&
      !detail.toLowerCase().includes("reference:") &&
      Number(response?.status || 0) >= 500
    ) {
      response.data.detail = `${detail} Reference: ${requestReference}.`;
    }

    if (Number(response?.status || 0) === 403 && code === "account_disabled") {
      clearAccessToken();
      dispatchAuthEvent("auth:forced-logout", {
        message: "This account is disabled. Contact an administrator.",
      });
      return Promise.reject(error);
    }

    if (Number(response?.status || 0) === 403 && code === "forbidden") {
      dispatchAuthEvent("auth:refresh-profile");
    }

    const isAuthEndpoint = url.includes("/api/auth/login") || url.includes("/api/auth/refresh");
    const shouldRefresh = Number(response?.status || 0) === 401 && !original._authRetry && !isAuthEndpoint;

    if (shouldRefresh) {
      original._authRetry = true;
      try {
        const refreshed = await refreshAccessToken();
        original.headers = original.headers || {};
        original.headers.Authorization = `Bearer ${refreshed.access_token}`;
        return api(original);
      } catch {
        return Promise.reject(error);
      }
    }

    return Promise.reject(error);
  }
);

export default api;
