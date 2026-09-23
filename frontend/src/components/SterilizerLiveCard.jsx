import ReactECharts from "echarts-for-react";
import {
  formatPlantDateTime,
  plantDateTimeParts,
} from "../utils/plantTime";

function normaliseUnit(unit) {
  const value = String(unit || "bar").toLowerCase().trim();
  return value === "psi" ? "psi" : "bar";
}

function getYAxisConfig(unit) {
  const cleanUnit = normaliseUnit(unit);
  if (cleanUnit === "psi") {
    return { max: 50, interval: 10, formatter: (value) => Number(value).toFixed(0) };
  }
  return { max: 4, interval: 0.5, formatter: (value) => Number(value).toFixed(1) };
}

function formatDateTimeLabel(value, index) {
  const parts = plantDateTimeParts(value);
  if (!parts) return "";
  const { year, month, day, hour: hours, minute: minutes } = parts;

  if (index === 0 || (hours === "00" && minutes === "00")) {
    return `${year}-${month}-${day}\n${hours}:${minutes}`;
  }

  return `${hours}:${minutes}`;
}

function getStatusClass(status) {
  const s = String(status || "").toLowerCase();
  if (s === "active") return "normal";
  if (s === "recent_cycle_detected") return "warning";
  return "abnormal";
}

function getStatusLabel(status) {
  const s = String(status || "").toLowerCase();
  if (s === "active") return "Active";
  if (s === "recent_cycle_detected") return "Recent Cycle Detected";
  if (s === "idle") return "Idle";
  return "No Data";
}

export default function SterilizerLiveCard({ sterilizer, unit = "bar", compact = false }) {
  const points = sterilizer?.points || [];
  const status = sterilizer?.status || "no_data";
  const cleanUnit = normaliseUnit(unit);
  const yAxisConfig = getYAxisConfig(cleanUnit);

  const option = {
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255,255,255,0.98)",
      borderColor: "#b8c5ce",
      textStyle: { color: "#172734" },
      formatter: (params) => {
        const item = params?.[0];
        if (!item) return "";
        const time = formatPlantDateTime(item.axisValue);
        const value = Number(item.data?.[1]);
        return `${time}<br/>Pressure: ${Number.isFinite(value) ? value.toFixed(2) : "-"} ${cleanUnit}`;
      },
    },

    grid: {
      left: compact ? 58 : 90,
      right: compact ? 14 : 26,
      top: compact ? 18 : 42,
      bottom: compact ? 44 : 84,
      containLabel: false,
    },

    dataZoom: [
      { type: "inside" },
      {
        type: "slider",
        bottom: compact ? 2 : 6,
        height: compact ? 11 : 18,
        borderColor: "#afbec9",
        fillerColor: "rgba(47, 111, 143, 0.14)",
        dataBackground: {
          lineStyle: { color: "#7fa3b5", opacity: 0.8 },
          areaStyle: { color: "#dbe7ec", opacity: 0.55 },
        },
      },
    ],

    xAxis: {
      type: "time",
      axisLabel: {
        color: "#5d6d79",
        formatter: formatDateTimeLabel,
        hideOverlap: compact,
        margin: compact ? 6 : 12,
        fontSize: compact ? 10 : 12,
      },
      axisLine: { lineStyle: { color: "#9aabb6" } },
      axisTick: { show: true },
      splitLine: { show: false },
    },

    yAxis: {
      type: "value",
      min: 0,
      max: yAxisConfig.max,
      interval: yAxisConfig.interval,
      axisLabel: {
        color: "#5d6d79",
        formatter: yAxisConfig.formatter,
        fontSize: compact ? 10 : 12,
      },
      axisLine: { show: true, lineStyle: { color: "#9aabb6" } },
      splitLine: { lineStyle: { type: "dashed", color: "#dce4e9" } },
    },

    series: [
      {
        name: "Pressure",
        type: "line",
        showSymbol: false,
        z: 3,
        lineStyle: { width: compact ? 2 : 2.5, type: "solid", color: "#2f6f8f" },
        data: points.map((p) => [p.time, p.raw]),
      },
    ],
  };

  return (
    <div className={`dash-card live-card ${compact ? "compact-live-card" : ""}`}>
      <div className="live-card-header">
        <div>
          <div className="live-card-title">{sterilizer.sterilizer_name}</div>
        </div>

        <span className={`status-badge ${getStatusClass(status)}`}>
          {getStatusLabel(status)}
        </span>
      </div>

      {sterilizer?.error && points.length > 0 && (
        <div className="info-banner compact-live-warning">{sterilizer.error}</div>
      )}

      {points.length > 0 ? (
        <div className="live-chart-section">
          <div className="live-chart-container">
          <div className="live-chart-unit-label">
            {cleanUnit}
          </div>

          <div className="live-chart-axis-label">
            Pressure
          </div>

            <ReactECharts
              option={option}
              style={{ height: "100%", width: "100%" }}
              notMerge={true}
            />
          </div>
        </div>
      ) : (
        <div className="empty-state compact-live-empty">
          {sterilizer?.error || "No live data available for this sterilizer."}
        </div>
      )}
    </div>
  );
}
