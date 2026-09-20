import ReactECharts from "echarts-for-react";
import { usePageSessionState } from "../state/pageSessionStore";
import { plantDateTimeParts } from "../utils/plantTime";

function formatAxisDateTime(value, index) {
  const parts = plantDateTimeParts(value);
  if (!parts) return "";
  const { year, month, day, hour, minute } = parts;

  const dateLabel = `${year}-${month}-${day}`;
  const timeLabel = `${hour}:${minute}`;

  if (index === 0) return `${dateLabel}\n${timeLabel}`;
  if (hour === "00" && minute === "00") {
    return `${dateLabel}\n${timeLabel}`;
  }
  return timeLabel;
}

function normaliseUnit(unit) {
  const value = String(unit || "bar").toLowerCase().trim();
  return value === "psi" ? "psi" : "bar";
}

function getYAxisConfig(unit) {
  const cleanUnit = normaliseUnit(unit);

  if (cleanUnit === "psi") {
    return {
      max: 50,
      interval: 10,
      formatter: (value) => Number(value).toFixed(0),
      scoreLabelY: 46,
    };
  }

  return {
    max: 4,
    interval: 0.5,
    formatter: (value) => Number(value).toFixed(1),
    scoreLabelY: 3.72,
  };
}

function normaliseScoreBand(scoreBand, score) {
  const band = String(scoreBand || "").toLowerCase().trim();

  if (["excellent", "good", "fair", "poor", "critical"].includes(band)) {
    return band;
  }

  const value = Number(score);
  if (value >= 90) return "excellent";
  if (value >= 75) return "good";
  if (value >= 60) return "fair";
  if (value >= 50) return "poor";
  return "critical";
}

function scoreColor(score, scoreBand) {
  const band = normaliseScoreBand(scoreBand, score);

  if (band === "excellent" || band === "good") return "#16a34a";
  if (band === "fair") return "#d97706";
  return "#dc2626";
}

function buildScoreTextPoints(cycleResults, unit) {
  const yAxisConfig = getYAxisConfig(unit);

  return cycleResults
    .filter((cycle) => cycle.cycle_start && cycle.cycle_end)
    .map((cycle) => {
      const startMs = new Date(cycle.cycle_start).getTime();
      const endMs = new Date(cycle.cycle_end).getTime();
      const middleMs = startMs + (endMs - startMs) / 2;

      return {
        value: [new Date(middleMs).toISOString(), yAxisConfig.scoreLabelY],
        cycleNo: cycle.cycle_no,
        cycle,
        score: Number(cycle.score),
        scoreBand: cycle.score_band,
        itemStyle: {
          color: "transparent",
        },
        label: {
          color: scoreColor(Number(cycle.score), cycle.score_band),
        },
      };
    });
}

export default function ContinuousComparisonChart({ comparisonResult, unit = "bar", onCycleClick }) {
  const [showBenchmarkOverlay, setShowBenchmarkOverlay] = usePageSessionState(
    "comparison",
    "showBenchmarkOverlay",
    true
  );

  const data = comparisonResult?.continuous_overlay_chart || [];
  const cycleResults = comparisonResult?.cycle_results || [];

  if (!data.length) return <p>No analysis data available.</p>;

  const cleanUnit = normaliseUnit(unit);
  const yAxisConfig = getYAxisConfig(cleanUnit);
  const scoreTextPoints = buildScoreTextPoints(cycleResults, cleanUnit);

  const series = [
    {
      name: "Real-Time",
      type: "line",
      color: "#2f6f8f",
      lineStyle: { width: 2 },
      showSymbol: false,
      data: data.map((d) => [d.time, d.realtime_smooth]),
    },
    ...(showBenchmarkOverlay
      ? [
          {
            name: "Benchmark Overlay",
            type: "line",
            color: "#47765f",
            lineStyle: { type: "dashed", width: 2 },
            showSymbol: false,
            data: data.map((d) => [d.time, d.benchmark_overlay]),
          },
        ]
      : []),
    {
      name: "Cycle Score Text",
      type: "scatter",
      silent: false,
      cursor: "pointer",
      symbolSize: 1,
      tooltip: { show: false },
      label: {
        show: true,
        position: "top",
        distance: 8,
        fontSize: 12,
        fontWeight: 700,
        formatter: (params) => `${params.data.score.toFixed(1)}%`,
      },
      labelLayout: { hideOverlap: true },
      itemStyle: { color: "transparent" },
      data: scoreTextPoints,
    },
  ];

  const option = {
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255, 255, 255, 0.98)",
      borderColor: "#b8c5ce",
      textStyle: { color: "#172734" },
    },
    legend: {
      data: showBenchmarkOverlay
        ? ["Real-Time", "Benchmark Overlay"]
        : ["Real-Time"],
      bottom: 25,
      left: "center",
      textStyle: { color: "#4f6471", fontSize: 11 },
    },
    grid: {
      left: 78,
      right: "2%",
      top: 34,
      bottom: 88,
      containLabel: false,
    },
    xAxis: {
      type: "time",
      axisLabel: {
        color: "#5d6d79",
        hideOverlap: false,
        margin: 10,
        fontSize: 10,
        formatter: formatAxisDateTime,
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
      axisLabel: { color: "#5d6d79", fontSize: 10, formatter: yAxisConfig.formatter },
      axisLine: { show: true, lineStyle: { color: "#9aabb6" } },
      splitLine: { lineStyle: { type: "dashed", color: "#dce4e9" } },
    },
    dataZoom: [
      { type: "inside" },
      {
        type: "slider",
        bottom: 2,
        height: 14,
        borderColor: "#afbec9",
        fillerColor: "rgba(47, 111, 143, 0.14)",
      },
    ],
    series,
  };

  const chartEvents = {
    click: (params) => {
      if (params.seriesName === "Cycle Score Text" && params.data?.cycle) {
        onCycleClick?.(params.data.cycle);
      }
    },
  };

  return (
    <div className="continuous-chart-root">
      <div className="chart-toolbar">
        <div className="info-banner chart-info-banner">
          V4 bands: <strong>≥90</strong> Excellent, <strong>75–89</strong> Good, <strong>60–74</strong> Fair, <strong>50–59</strong> Poor, <strong>&lt;50</strong> Critical.
        </div>

        <button
          type="button"
          className="secondary-btn"
          onClick={() => setShowBenchmarkOverlay((prev) => !prev)}
        >
          {showBenchmarkOverlay ? "Hide Benchmark Overlay" : "Show Benchmark Overlay"}
        </button>
      </div>

      <div className="continuous-chart-box">
        <div className="chart-y-unit">{cleanUnit}</div>
        <div className="chart-y-label">Pressure</div>
        <ReactECharts option={option} style={{ height: "100%" }} notMerge={true} onEvents={chartEvents} />
      </div>
    </div>
  );
}
