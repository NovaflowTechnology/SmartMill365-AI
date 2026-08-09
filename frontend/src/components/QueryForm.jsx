function formatDateTimeWithOffset(value) {
  if (!value) return "";

  const date = new Date(value);
  const pad = (n) => String(n).padStart(2, "0");

  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const seconds = "00";

  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absOffset = Math.abs(offsetMinutes);
  const offsetHours = pad(Math.floor(absOffset / 60));
  const offsetMins = pad(absOffset % 60);

  return `${year}-${month}-${day}T${hours}:${minutes}:${seconds}${sign}${offsetHours}:${offsetMins}`;
}

function toDatetimeLocalValue(isoString) {
  if (!isoString) return "";

  const date = new Date(isoString);
  const pad = (n) => String(n).padStart(2, "0");

  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());

  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

export default function QueryForm({
  formData,
  setFormData,
  onDetect,
  loading,
  submitLabel = "Detect Cycles",
}) {
  const updateField = (key, value) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="query-form-container">
      <div className="query-section">
        <div className="field-group">
          <label>Bucket</label>
          <input
            value={formData.bucket}
            onChange={(e) => updateField("bucket", e.target.value)}
            placeholder="Mill"
          />
        </div>

        <div className="field-group">
          <label>Measurement</label>
          <input
            value={formData.measurement}
            onChange={(e) => updateField("measurement", e.target.value)}
            placeholder="PSTR"
          />
        </div>

        <div className="field-group">
          <label>Field / Channel</label>
          <input
            value={formData.field}
            onChange={(e) => updateField("field", e.target.value)}
            placeholder="ch4"
          />
        </div>

        <div className="field-group">
          <label>Tag ID</label>
          <input
            placeholder="e.g. SAMYSK_PSTR_240004"
            value={formData.tag_id}
            onChange={(e) => updateField("tag_id", e.target.value)}
          />
        </div>

        <div className="field-group">
          <label>Source Unit</label>
          <select
            value={formData.source_unit}
            onChange={(e) => updateField("source_unit", e.target.value)}
          >
            <option value="bar">bar</option>
            <option value="psi">psi</option>
          </select>
        </div>

        <div className="field-group">
          <label>Start Time</label>
          <input
            type="datetime-local"
            value={toDatetimeLocalValue(formData.start_time)}
            onChange={(e) =>
              updateField("start_time", formatDateTimeWithOffset(e.target.value))
            }
          />
        </div>

        <div className="field-group">
          <label>Stop Time</label>
          <input
            type="datetime-local"
            value={toDatetimeLocalValue(formData.stop_time)}
            onChange={(e) =>
              updateField("stop_time", formatDateTimeWithOffset(e.target.value))
            }
          />
        </div>

        <div className="field-group">
          <label>Smooth Window</label>
          <input
            type="number"
            min="1"
            value={formData.smooth_window}
            onChange={(e) => updateField("smooth_window", Number(e.target.value))}
          />
        </div>
      </div>

      <button
        className="btn-primary-full"
        onClick={onDetect}
        disabled={loading}
      >
        {loading ? "Processing..." : submitLabel}
      </button>
    </div>
  );
}