import ReactECharts from "echarts-for-react";

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
  const date = new Date(value);
  const pad = (n) => String(n).padStart(2, "0");

  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());

  if (index === 0 || (date.getHours() === 0 && date.getMinutes() === 0)) {
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

export default function SterilizerLiveCard({ sterilizer, unit = "bar" }) {
  const points = sterilizer?.points || [];
  const cycles = sterilizer?.cycles || [];
  const latestValue = sterilizer?.latest_value;
  const status = sterilizer?.status || "no_data";
  const cleanUnit = normaliseUnit(unit);
  const yAxisConfig = getYAxisConfig(cleanUnit);

  const option = {
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255,255,255,0.98)",
      borderColor: "#e2e8f0",
      textStyle: { color: "#1e293b" },
      formatter: (params) => {
        const item = params?.[0];
        if (!item) return "";
        const time = new Date(item.axisValue).toLocaleString();
        const value = Number(item.data?.[1]);
        return `${time}<br/>Pressure: ${Number.isFinite(value) ? value.toFixed(2) : "-"} ${cleanUnit}`;
      },
    },

    grid: {
      left: 90,
      right: 26,
      top: 42,
      bottom: 58,
      containLabel: false,
    },

    xAxis: {
      type: "time",
      axisLabel: {
        color: "#64748b",
        formatter: formatDateTimeLabel,
        hideOverlap: false,
        margin: 12,
      },
      axisLine: { lineStyle: { color: "#94a3b8" } },
      axisTick: { show: true },
      splitLine: { show: false },
    },

    yAxis: {
      type: "value",
      min: 0,
      max: yAxisConfig.max,
      interval: yAxisConfig.interval,
      axisLabel: {
        color: "#64748b",
        formatter: yAxisConfig.formatter,
      },
      axisLine: { show: true, lineStyle: { color: "#94a3b8" } },
      splitLine: { lineStyle: { type: "dashed", color: "#e2e8f0" } },
    },

    series: [
      {
        name: "Pressure",
        type: "line",
        showSymbol: false,
        z: 3,
        lineStyle: { width: 2.5, type: "solid", color: "#2563eb" },
        data: points.map((p) => [p.time, p.raw]),
      },
    ],
  };

  return (
    <div className="dash-card live-card">
      <div className="live-card-header">
        <div>
          <div className="live-card-title">{sterilizer.sterilizer_name}</div>
        </div>

        <span className={`status-badge ${getStatusClass(status)}`}>
          {getStatusLabel(status)}
        </span>
      </div>

      {sterilizer?.error && <div className="info-banner">{sterilizer.error}</div>}

      <div className="live-mini-stats">
        <div className="live-stat-box">
          <div className="live-stat-label">Latest Value</div>
          <div className="live-stat-value">
            {latestValue == null ? "-" : `${Number(latestValue).toFixed(2)} ${cleanUnit}`}
          </div>
        </div>

        <div className="live-stat-box">
          <div className="live-stat-label">Detected Cycles</div>
          <div className="live-stat-value">{cycles.length}</div>
        </div>
      </div>

      {points.length > 0 ? (
        <div style={{ width: "100%", height: 430, position: "relative" }}>
          <div
            style={{
              position: "absolute",
              top: 10,
              left: 92,
              zIndex: 2,
              fontSize: 13,
              fontWeight: 700,
              color: "#64748b",
              pointerEvents: "none",
            }}
          >
            {cleanUnit}
          </div>

          <div
            style={{
              position: "absolute",
              left: 18,
              top: "46%",
              zIndex: 2,
              transform: "translateY(-50%) rotate(-90deg)",
              transformOrigin: "center",
              fontSize: 14,
              fontWeight: 700,
              color: "#64748b",
              pointerEvents: "none",
            }}
          >
            Pressure
          </div>

          <ReactECharts option={option} style={{ height: "100%" }} notMerge={true} />
        </div>
      ) : (
        <div className="empty-state">No live data available for this sterilizer.</div>
      )}
    </div>
  );
}
