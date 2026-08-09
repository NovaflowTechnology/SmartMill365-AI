import axios from "axios";

const rawEnvBaseUrl = import.meta.env.VITE_API_BASE_URL;

const cleanEnvBaseUrl =
  rawEnvBaseUrl && rawEnvBaseUrl.trim() !== ""
    ? rawEnvBaseUrl.trim()
    : null;

const api = axios.create({
  baseURL: cleanEnvBaseUrl
    ? cleanEnvBaseUrl
    : import.meta.env.DEV
      ? "http://127.0.0.1:8000"
      : "",
  timeout: 300000,
});

export default api;