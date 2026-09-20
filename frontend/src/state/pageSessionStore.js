import { useCallback, useEffect, useState } from "react";

const STORAGE_PREFIX = "sterilizer-page-session:v1";
const memoryState = new Map();
const listeners = new Map();
const runningTasks = new Map();
const latestTaskTokens = new Map();

function stateKey(page, field) {
  return `${page}:${field}`;
}

function storageKey(page, field) {
  return `${STORAGE_PREFIX}:${stateKey(page, field)}`;
}

function resolveInitialValue(initialValue) {
  return typeof initialValue === "function" ? initialValue() : initialValue;
}

function readStoredValue(page, field) {
  try {
    const raw = window.sessionStorage.getItem(storageKey(page, field));
    return raw === null ? undefined : JSON.parse(raw);
  } catch (error) {
    console.warn(`Unable to restore ${page}.${field} from this tab session.`, error);
    return undefined;
  }
}

function writeStoredValue(page, field, value) {
  try {
    if (value === undefined) {
      window.sessionStorage.removeItem(storageKey(page, field));
      return;
    }
    window.sessionStorage.setItem(storageKey(page, field), JSON.stringify(value));
  } catch (error) {
    // Navigation caching still works when a value is too large for sessionStorage.
    console.warn(`Unable to persist ${page}.${field} in this tab session.`, error);
  }
}

function getCurrentValue(page, field, initialValue, persist) {
  const key = stateKey(page, field);
  if (memoryState.has(key)) return memoryState.get(key);

  const stored = persist ? readStoredValue(page, field) : undefined;
  const value = stored === undefined ? resolveInitialValue(initialValue) : stored;
  memoryState.set(key, value);
  return value;
}

function notify(key, value) {
  (listeners.get(key) || []).forEach((listener) => listener(value));
}

/**
 * Drop-in page state for values that must survive route unmounts.
 * `persist: true` also restores JSON-safe values after a refresh in this tab.
 * Heavy time-series data should use `persist: false` and remain memory-only.
 */
export function usePageSessionState(
  page,
  field,
  initialValue,
  { persist = true } = {}
) {
  const key = stateKey(page, field);
  const [value, setLocalValue] = useState(() =>
    getCurrentValue(page, field, initialValue, persist)
  );

  useEffect(() => {
    const pageListeners = listeners.get(key) || new Set();
    pageListeners.add(setLocalValue);
    listeners.set(key, pageListeners);

    return () => {
      const currentListeners = listeners.get(key);
      currentListeners?.delete(setLocalValue);
      if (currentListeners && currentListeners.size === 0) listeners.delete(key);
    };
  }, [field, key, page, persist]);

  const setValue = useCallback(
    (nextValue) => {
      const currentValue = getCurrentValue(page, field, initialValue, persist);
      const resolvedValue =
        typeof nextValue === "function" ? nextValue(currentValue) : nextValue;

      memoryState.set(key, resolvedValue);
      if (persist) writeStoredValue(page, field, resolvedValue);
      notify(key, resolvedValue);
      return resolvedValue;
    },
    [field, initialValue, key, page, persist]
  );

  return [value, setValue];
}


/**
 * Updates cached page state from another route without requiring that route to
 * be mounted. This is useful when one workflow saves data that another cached
 * page also displays.
 */
export function setPageSessionValue(
  page,
  field,
  nextValue,
  { persist = true } = {}
) {
  const key = stateKey(page, field);
  const currentValue = memoryState.has(key)
    ? memoryState.get(key)
    : persist
      ? readStoredValue(page, field)
      : undefined;

  const resolvedValue =
    typeof nextValue === "function" ? nextValue(currentValue) : nextValue;

  if (resolvedValue === undefined) {
    memoryState.delete(key);
  } else {
    memoryState.set(key, resolvedValue);
  }
  if (persist) writeStoredValue(page, field, resolvedValue);
  notify(key, resolvedValue);
  return resolvedValue;
}

/** Deduplicates an operation while its request remains active across navigation. */
export function runPageTask(taskKey, task) {
  if (runningTasks.has(taskKey)) return runningTasks.get(taskKey);

  const operation = Promise.resolve()
    .then(task)
    .finally(() => runningTasks.delete(taskKey));

  runningTasks.set(taskKey, operation);
  return operation;
}

/**
 * Runs every request, but only lets the newest request for a scope update UI state.
 * This prevents a slower, older response from replacing a newer user selection.
 */
export function runLatestPageTask(scopeKey, task) {
  const token = Symbol(scopeKey);
  latestTaskTokens.set(scopeKey, token);
  const controls = {
    isLatest: () => latestTaskTokens.get(scopeKey) === token,
  };

  return Promise.resolve()
    .then(() => task(controls))
    .finally(() => {
      if (latestTaskTokens.get(scopeKey) === token) {
        latestTaskTokens.delete(scopeKey);
      }
    });
}
