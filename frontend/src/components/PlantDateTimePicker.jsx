import DatePicker from "react-datepicker";

import { toPlantRequestDateTime } from "../utils/plantTime";

const HOURS = Array.from({ length: 24 }, (_, index) =>
  String(index).padStart(2, "0")
);
const MINUTES = Array.from({ length: 60 }, (_, index) =>
  String(index).padStart(2, "0")
);

function parseWallClock(value) {
  const canonical = toPlantRequestDateTime(value);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(canonical);
  if (!match) return null;
  return new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5]),
    0,
    0
  );
}

function formatWallClock(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`;
}

function ScrollableTimeSelector({ value = "00:00", onChange = () => {} }) {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  const hour = match && HOURS.includes(match[1]) ? match[1] : "00";
  const minute = match && MINUTES.includes(match[2]) ? match[2] : "00";

  const updateTime = (nextHour, nextMinute) => {
    onChange(`${nextHour}:${nextMinute}`);
  };

  return (
    <div
      className="plant-time-scroll-selector"
      role="group"
      aria-label="Select time in 24-hour format"
    >
      <label className="plant-time-scroll-column">
        <span>Hour</span>
        <select
          value={hour}
          size={7}
          onChange={(event) => updateTime(event.target.value, minute)}
          aria-label="Hour"
        >
          {HOURS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>

      <span className="plant-time-scroll-separator" aria-hidden="true">
        :
      </span>

      <label className="plant-time-scroll-column">
        <span>Minute</span>
        <select
          value={minute}
          size={7}
          onChange={(event) => updateTime(hour, event.target.value)}
          aria-label="Minute"
        >
          {MINUTES.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

export default function PlantDateTimePicker({
  value,
  onChange,
  id,
  disabled = false,
}) {
  return (
    <DatePicker
      id={id}
      selected={parseWallClock(value)}
      onChange={(date) => onChange(formatWallClock(date))}
      showTimeInput
      timeInputLabel="Time"
      customTimeInput={<ScrollableTimeSelector />}
      dateFormat="dd/MM/yyyy HH:mm"
      placeholderText="DD/MM/YYYY HH:mm"
      className="plant-datetime-picker-input"
      calendarClassName="plant-datetime-picker-calendar"
      wrapperClassName="plant-datetime-picker-wrapper"
      popperPlacement="bottom-start"
      disabled={disabled}
      strictParsing
      shouldCloseOnSelect={false}
      showPopperArrow={false}
    />
  );
}
