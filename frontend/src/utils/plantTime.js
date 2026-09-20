export const PLANT_TIME_ZONE =
  String(import.meta.env?.VITE_PLANT_TIMEZONE || "Asia/Kuala_Lumpur").trim() ||
  "Asia/Kuala_Lumpur";

const LOCAL_DATE_TIME_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?$/;
const DISPLAY_DATE_TIME_PATTERN =
  /^(\d{2})\/(\d{2})\/(\d{4})[ ,]+(\d{1,2}):(\d{2})(?::(\d{2}))?$/;
const LEGACY_US_DATE_TIME_PATTERN =
  /^(\d{1,2})\/(\d{1,2})\/(\d{4})[ ,]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])$/;
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const DISPLAY_DATE_PATTERN = /^(\d{2})\/(\d{2})\/(\d{4})$/;
const EXPLICIT_TIME_ZONE_PATTERN = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;

function isValidCalendarDate(year, month, day) {
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return (
    date.getUTCFullYear() === Number(year) &&
    date.getUTCMonth() === Number(month) - 1 &&
    date.getUTCDate() === Number(day)
  );
}

function buildPlantRequestDateTime(match) {
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] || "00");
  if (
    !isValidCalendarDate(match[1], match[2], match[3]) ||
    hour < 0 ||
    hour > 23 ||
    minute < 0 ||
    minute > 59 ||
    second < 0 ||
    second > 59
  ) {
    return "";
  }
  return `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6] || "00"}`;
}

function parseDisplayDateTime(text) {
  const match = text.match(DISPLAY_DATE_TIME_PATTERN);
  if (!match) return "";

  const [, day, month, year, hour, minute, second = "00"] = match;
  const numericDay = Number(day);
  const numericMonth = Number(month);
  const numericHour = Number(hour);
  const numericMinute = Number(minute);
  const numericSecond = Number(second);

  if (
    !isValidCalendarDate(year, numericMonth, numericDay) ||
    numericHour < 0 ||
    numericHour > 23 ||
    numericMinute < 0 ||
    numericMinute > 59 ||
    numericSecond < 0 ||
    numericSecond > 59
  ) {
    return "";
  }

  return `${year}-${month}-${day}T${String(numericHour).padStart(2, "0")}:${minute}:${second}`;
}

// Older browser-native datetime controls may have left a locale-formatted
// value such as "08/17/2026 08:49 PM" in session storage. Accept that legacy
// value once so it can be rendered and submitted in the current DD/MM/YYYY
// 24-hour format. New manual input remains DD/MM/YYYY HH:mm.
function parseLegacyUsDateTime(text) {
  const match = text.match(LEGACY_US_DATE_TIME_PATTERN);
  if (!match) return "";

  const [, month, day, year, displayHour, minute, second = "00", meridiem] = match;
  const numericMonth = Number(month);
  const numericDay = Number(day);
  const numericDisplayHour = Number(displayHour);
  const numericMinute = Number(minute);
  const numericSecond = Number(second);

  if (
    !isValidCalendarDate(year, numericMonth, numericDay) ||
    numericDisplayHour < 1 ||
    numericDisplayHour > 12 ||
    numericMinute < 0 ||
    numericMinute > 59 ||
    numericSecond < 0 ||
    numericSecond > 59
  ) {
    return "";
  }

  const isPm = meridiem.toUpperCase() === "PM";
  const hour = numericDisplayHour % 12 + (isPm ? 12 : 0);
  return `${year}-${String(numericMonth).padStart(2, "0")}-${String(numericDay).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String(numericMinute).padStart(2, "0")}:${String(numericSecond).padStart(2, "0")}`;
}

function parseSupportedDisplayDateTime(text) {
  return parseDisplayDateTime(text) || parseLegacyUsDateTime(text);
}

export function plantDateTimeParts(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: PLANT_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

export function formatDateTimeLocalForPlant(value = new Date()) {
  const parts = plantDateTimeParts(value);
  if (!parts) return "";
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function defaultPlantDateTime(hoursAgo = 0) {
  return formatDateTimeLocalForPlant(
    new Date(Date.now() - Number(hoursAgo || 0) * 60 * 60 * 1000)
  );
}

export function toPlantDateTimeInput(value) {
  const text = String(value || "").trim();
  const localMatch = text.match(LOCAL_DATE_TIME_PATTERN);
  if (localMatch) return text.replace(" ", "T").slice(0, 16);

  const displayValue = parseSupportedDisplayDateTime(text);
  if (displayValue) return displayValue.slice(0, 16);

  return EXPLICIT_TIME_ZONE_PATTERN.test(text)
    ? formatDateTimeLocalForPlant(text)
    : "";
}

export function toPlantRequestDateTime(value) {
  const text = String(value || "").trim().replace(" ", "T");
  const match = text.match(LOCAL_DATE_TIME_PATTERN);
  if (match) return buildPlantRequestDateTime(match);

  const displayValue = parseSupportedDisplayDateTime(String(value || "").trim());
  if (displayValue) return displayValue;

  // Older saved form state may contain an explicit UTC/offset timestamp.
  // Convert that instant back to the configured plant wall time before the
  // request is validated and sent to the backend.
  if (EXPLICIT_TIME_ZONE_PATTERN.test(text)) {
    const plantLocalValue = formatDateTimeLocalForPlant(text);
    return plantLocalValue ? `${plantLocalValue}:00` : "";
  }

  return "";
}

export function isValidPlantDateTimeRange(startValue, stopValue) {
  const start = toPlantRequestDateTime(startValue);
  const stop = toPlantRequestDateTime(stopValue);
  return Boolean(start && stop && start < stop);
}

export function formatPlantDateTimeInputDisplay(value) {
  const canonical = toPlantRequestDateTime(value);
  const match = canonical.match(LOCAL_DATE_TIME_PATTERN);
  if (!match) return String(value || "");
  return `${match[3]}/${match[2]}/${match[1]} ${match[4]}:${match[5]}`;
}

export function toPlantRequestDate(value) {
  const text = String(value || "").trim();
  const isoMatch = text.match(ISO_DATE_PATTERN);
  if (isoMatch) {
    return isValidCalendarDate(isoMatch[1], isoMatch[2], isoMatch[3])
      ? text
      : "";
  }

  const displayMatch = text.match(DISPLAY_DATE_PATTERN);
  if (
    !displayMatch ||
    !isValidCalendarDate(displayMatch[3], displayMatch[2], displayMatch[1])
  ) {
    return "";
  }
  return `${displayMatch[3]}-${displayMatch[2]}-${displayMatch[1]}`;
}

export function formatPlantDateInputDisplay(value) {
  const canonical = toPlantRequestDate(value);
  const match = canonical.match(ISO_DATE_PATTERN);
  if (!match) return String(value || "");
  return `${match[3]}/${match[2]}/${match[1]}`;
}

export function formatPlantDateTime(value, options = {}) {
  if (!value) return "–";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: PLANT_TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    ...options,
  }).format(date);
}

export function formatPlantDate(value, options = {}) {
  if (!value) return "–";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: PLANT_TIME_ZONE,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    ...options,
  }).format(date);
}

export function formatPlantTime(value, options = {}) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: PLANT_TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    ...options,
  }).format(date);
}

export function defaultPlantDate(daysAgo = 0) {
  return defaultPlantDateTime(Number(daysAgo || 0) * 24).slice(0, 10);
}
