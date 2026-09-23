import DatePicker from "react-datepicker";

import { toPlantRequestDate } from "../utils/plantTime";

function parseWallClockDate(value) {
  const canonical = toPlantRequestDate(value);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(canonical);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function formatWallClockDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )}`;
}

export default function PlantDatePicker({ value, onChange, id, disabled = false }) {
  return (
    <DatePicker
      id={id}
      selected={parseWallClockDate(value)}
      onChange={(date) => onChange(formatWallClockDate(date))}
      dateFormat="dd/MM/yyyy"
      placeholderText="DD/MM/YYYY"
      className="plant-date-picker-input"
      calendarClassName="plant-datetime-picker-calendar"
      wrapperClassName="plant-date-picker-wrapper"
      popperPlacement="bottom-start"
      disabled={disabled}
      strictParsing
      showPopperArrow={false}
    />
  );
}
