import React, { useEffect, useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useNavigate } from "react-router-dom";
import api from "../api";
import { useAuth } from "../auth/AuthContext";
import TopNav from "../components/TopNav";
import PlantDatePicker from "../components/PlantDatePicker";
import {
  defaultPlantDate,
  formatPlantDate,
  formatPlantDateTime,
} from "../utils/plantTime";
import {
  runLatestPageTask,
  usePageSessionState,
} from "../state/pageSessionStore";

const BAND_ORDER = ["excellent", "good", "fair", "poor", "critical"];
const BAND_META = {
  excellent: { label: "Excellent", range: "≥90", color: "#2563eb" },
  good: { label: "Good", range: "75–89", color: "#16a34a" },
  fair: { label: "Fair", range: "60–74", color: "#f59e0b" },
  poor: { label: "Poor", range: "50–59", color: "#f97316" },
  critical: { label: "Critical", range: "<50", color: "#dc2626" },
};

function defaultReportDate() {
  return defaultPlantDate(1);
}

function formatScore(value, digits = 1) {
  return value === null || value === undefined || Number.isNaN(Number(value))
    ? "–"
    : Number(value).toFixed(digits);
}

function formatDate(value, options = {}) {
  return formatPlantDate(value, options);
}

function formatDateTime(value) {
  return formatPlantDateTime(value);
}

function getFriendlySterilizerName(name, field) {
  if (name) return name;

  const match = /^stp(\d+)$/i.exec(String(field || "").trim());
  return match ? `Sterilizer ${Number(match[1])}` : "–";
}

function percentage(count, total) {
  return total ? `${((count / total) * 100).toFixed(1)}%` : "0.0%";
}

function countAndPercentage(band, total) {
  const count = band?.count || 0;
  return `${count} (${percentage(count, total)})`;
}

function Delta({ value, suffix = "" }) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return <span className="report-delta neutral">–</span>;
  }
  const numeric = Number(value);
  const className = numeric > 0 ? "positive" : numeric < 0 ? "negative" : "neutral";
  return (
    <span className={`report-delta ${className}`}>
      {numeric > 0 ? "+" : ""}
      {numeric.toFixed(suffix === " pts" ? 1 : 0)}{suffix}
    </span>
  );
}

function KpiCard({ label, value, suffix, tone }) {
  return (
    <div className={`report-kpi-card ${tone}`}>
      <span className="report-kpi-label">{label}</span>
      <div className="report-kpi-value-row">
        <strong>{value}</strong>
        {suffix && <span>{suffix}</span>}
      </div>
    </div>
  );
}

class ReportRenderBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("Daily Report rendering failed.", error, errorInfo);
  }

  componentDidUpdate(previousProps) {
    if (
      previousProps.resetKey !== this.props.resetKey &&
      this.state.error
    ) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <section className="dash-card daily-report-empty no-print">
          <strong>The generated report could not be displayed</strong>
          <p>
            Clear this report and generate it again. The rest of the application
            remains available.
          </p>
          <button type="button" className="btn-primary" onClick={this.props.onReset}>
            Clear Report and Retry
          </button>
        </section>
      );
    }
    return this.props.children;
  }
}

function SectionHeader({ number, title }) {
  return (
    <div className="report-section-header">
      <span>{number}.</span> {title}
    </div>
  );
}

function PrintDistributionChart({ summary }) {
  const total = summary.total_cycles || 0;
  const radius = 45;
  const circumference = 2 * Math.PI * radius;
  const { segments } = BAND_ORDER.reduce(
    (state, key) => {
      const count = summary.bands?.[key]?.count || 0;
      const share = total ? (count / total) * 100 : 0;
      const end = state.cursor + share;
      return {
        cursor: end,
        segments: [
          ...state.segments,
          { key, count, share, start: state.cursor, end },
        ],
      };
    },
    { cursor: 0, segments: [] }
  );
  return (
    <div className="print-only-chart print-distribution-chart" aria-hidden="true">
      <div className="print-donut-wrap">
        <svg className="print-donut-svg" viewBox="0 0 120 120">
          <circle cx="60" cy="60" r={radius} fill="none" stroke="#e2e8f0" strokeWidth="20" />
          {total > 0 && segments.filter((item) => item.share > 0).map((item) => (
            <circle
              cx="60"
              cy="60"
              r={radius}
              fill="none"
              stroke={BAND_META[item.key].color}
              strokeWidth="20"
              strokeDasharray={`${(item.share / 100) * circumference} ${circumference}`}
              strokeDashoffset={-(item.start / 100) * circumference}
              transform="rotate(-90 60 60)"
              key={item.key}
            />
          ))}
          <circle cx="60" cy="60" r="32" fill="#ffffff" />
          <text x="60" y="59" textAnchor="middle" className="print-donut-total">{total}</text>
          <text x="60" y="72" textAnchor="middle" className="print-donut-caption">Total Cycles</text>
        </svg>
      </div>
      <div className="print-distribution-legend">
        {segments.map((item) => (
          <div className="print-legend-row" key={item.key}>
            <svg className="print-legend-swatch" viewBox="0 0 10 10" aria-hidden="true">
              <rect width="10" height="10" rx="2" fill={BAND_META[item.key].color} />
            </svg>
            <span className="print-legend-label">
              {BAND_META[item.key].label} ({BAND_META[item.key].range})
            </span>
            <strong>{item.count}</strong>
            <span>{item.share.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PrintShiftChart({ comparison }) {
  const shifts = [comparison.morning, comparison.night];
  const rawMaximum = Math.max(1, ...shifts.map((item) => item.total_cycles || 0));
  const maximum = Math.ceil(rawMaximum / 5) * 5;
  const width = 460;
  const height = 175;
  const plot = { left: 42, right: 12, top: 12, bottom: 42 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const barWidth = 54;
  const xPositions = [plot.left + plotWidth * 0.3, plot.left + plotWidth * 0.7];
  const y = (value) => plot.top + (1 - Number(value) / maximum) * plotHeight;
  const tickValues = [maximum, maximum * 0.75, maximum * 0.5, maximum * 0.25, 0];

  return (
    <div className="print-only-chart print-shift-chart" aria-hidden="true">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Morning and night shift total cycle comparison">
        <text x="3" y="10" className="print-shift-axis-label">Cycles</text>
        {tickValues.map((value) => (
          <g key={value}>
            <line
              x1={plot.left}
              x2={width - plot.right}
              y1={y(value)}
              y2={y(value)}
              className="print-shift-svg-grid-line"
            />
            <text x={plot.left - 7} y={y(value) + 3} textAnchor="end">{Math.round(value)}</text>
          </g>
        ))}
        {shifts.map((item, index) => {
          const value = item.total_cycles || 0;
          const barTop = y(value);
          return (
            <g key={item.label}>
              <rect
                x={xPositions[index] - barWidth / 2}
                y={barTop}
                width={barWidth}
                height={plot.top + plotHeight - barTop}
                rx="2"
                fill={index === 0 ? "#2563eb" : "#f97316"}
              />
              <text x={xPositions[index]} y={barTop - 6} textAnchor="middle" className="print-shift-value">{value}</text>
              <text x={xPositions[index]} y={height - 20} textAnchor="middle" className="print-shift-name">{item.label}</text>
              <text x={xPositions[index]} y={height - 8} textAnchor="middle" className="print-shift-time">{item.start} - {item.end}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function DistributionChart({ summary }) {
  const total = summary.total_cycles || 0;
  const data = BAND_ORDER.map((key) => ({
    name: `${BAND_META[key].label} (${BAND_META[key].range})`,
    value: summary.bands?.[key]?.count || 0,
    itemStyle: { color: BAND_META[key].color },
  }));
  const option = {
    animation: false,
    tooltip: { trigger: "item", formatter: "{b}: {c} cycles ({d}%)" },
    legend: {
      orient: "vertical",
      right: 0,
      top: "middle",
      textStyle: { fontSize: 10, color: "#334155" },
    },
    title: {
      text: String(total),
      subtext: "Total Cycles",
      left: "31%",
      top: "39%",
      textAlign: "center",
      textStyle: { fontSize: 25, fontWeight: 900, color: "#0f172a" },
      subtextStyle: { fontSize: 10, color: "#64748b" },
    },
    series: [
      {
        type: "pie",
        radius: ["48%", "72%"],
        center: ["32%", "52%"],
        avoidLabelOverlap: true,
        label: {
          show: true,
          formatter: ({ percent }) => (percent > 0 ? `${percent.toFixed(1)}%` : ""),
          fontSize: 10,
          fontWeight: 700,
        },
        data,
      },
    ],
  };
  return (
    <ReactECharts
      className="daily-report-chart screen-report-chart report-distribution-echart"
      option={option}
      style={{ height: 245 }}
      notMerge
    />
  );
}

function ShiftChart({ comparison }) {
  const option = {
    animation: false,
    grid: { top: 26, left: 40, right: 15, bottom: 40 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: [comparison.morning.label, comparison.night.label],
      axisLabel: { fontSize: 10, fontWeight: 700 },
    },
    yAxis: {
      type: "value",
      min: 0,
      axisLabel: { fontSize: 9 },
      splitLine: { lineStyle: { color: "#e2e8f0", type: "dashed" } },
    },
    series: [
      {
        type: "bar",
        barWidth: 42,
        label: { show: true, position: "top", fontWeight: 800 },
        data: [
          { value: comparison.morning.total_cycles, itemStyle: { color: "#2563eb" } },
          { value: comparison.night.total_cycles, itemStyle: { color: "#f97316" } },
        ],
      },
    ],
  };
  return (
    <ReactECharts
      className="daily-report-chart screen-report-chart report-shift-echart"
      option={option}
      style={{ height: 220 }}
      notMerge
    />
  );
}

function PerformanceTable({ sterilizers }) {
  const rows = [
    ["Total Cycles", (item) => item.total_cycles],
    ["Cycle Score (Avg)", (item) => formatScore(item.average_score)],
    ["Best Cycle Score", (item) => formatScore(item.best_score)],
    ["Worst Cycle Score", (item) => formatScore(item.worst_score)],
    ...BAND_ORDER.map((key) => [
      `${BAND_META[key].label} (${BAND_META[key].range})`,
      (item) => countAndPercentage(item.bands?.[key], item.total_cycles),
    ]),
    ["Status", (item) => item.status],
  ];
  return (
    <div className="report-table-wrap">
      <table className="report-table performance-table">
        <thead>
          <tr>
            <th>Metric</th>
            {sterilizers.map((item) => (
              <th key={item.field}>
                {item.sterilizer_name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, getValue]) => (
            <tr key={label}>
              <th>{label}</th>
              {sterilizers.map((item) => {
                const value = getValue(item);
                const isStatus = label === "Status";
                return (
                  <td key={item.field}>
                    {isStatus ? (
                      <span className={`report-status ${String(value).toLowerCase().replace(" ", "-")}`}>
                        {value}
                      </span>
                    ) : (
                      value
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShiftTable({ comparison }) {
  const morning = comparison.morning;
  const night = comparison.night;
  const rows = [
    ["Total Cycles", morning.total_cycles, night.total_cycles, comparison.night_minus_morning.total_cycles],
    ["Avg Cycle Score", morning.average_score, night.average_score, comparison.night_minus_morning.average_score],
    ...BAND_ORDER.map((key) => [
      `${BAND_META[key].label} (${BAND_META[key].range})`,
      countAndPercentage(morning.bands?.[key], morning.total_cycles),
      countAndPercentage(night.bands?.[key], night.total_cycles),
      (night.bands?.[key]?.count || 0) - (morning.bands?.[key]?.count || 0),
    ]),
  ];
  return (
    <div className="report-table-wrap">
      <table className="report-table shift-report-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>
              {morning.label}
              <small>{morning.start} – {morning.end}</small>
            </th>
            <th>
              {night.label}
              <small>{night.start} – {night.end}</small>
            </th>
            <th>Difference<br /><small>Night − Morning</small></th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, morningValue, nightValue, difference]) => (
            <tr key={label}>
              <th>{label}</th>
              <td>{typeof morningValue === "number" && label === "Avg Cycle Score" ? formatScore(morningValue) : morningValue ?? "–"}</td>
              <td>{typeof nightValue === "number" && label === "Avg Cycle Score" ? formatScore(nightValue) : nightValue ?? "–"}</td>
              <td><Delta value={difference} suffix={label === "Avg Cycle Score" ? " pts" : ""} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IncidentHistoryAppendix({ report }) {
  const incidents = report.incident_history || [];
  if (!incidents.length) return null;

  return (
    <section className="report-print-page incident-appendix-page">
      <header className="incident-appendix-header">
        <div>
          <span>DAILY REPORT APPENDIX</span>
          <h2>Full-Day Incident History</h2>
          <p>
            {report.plant?.data_id} · {formatDate(`${report.report_date}T00:00:00`)} ·
            warned cycles remain included in every KPI and calculation
          </p>
        </div>
        <strong>{incidents.length} incidents</strong>
      </header>

      <div className="incident-summary-grid">
        <div><span>Total</span><strong>{report.incident_summary?.total_incidents || 0}</strong></div>
        <div><span>Performance</span><strong>{report.incident_summary?.performance_cycles || 0}</strong></div>
        <div><span>Data-quality cycles</span><strong>{report.incident_summary?.data_quality_cycles || 0}</strong></div>
        <div><span>Data-availability gaps</span><strong>{report.incident_summary?.data_availability_incidents || 0}</strong></div>
        <div><span>Critical cycles</span><strong>{report.incident_summary?.critical_cycles || 0}</strong></div>
      </div>

      <div className="incident-table-wrap">
        <table className="incident-history-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Shift</th>
              <th>Sterilizer / Cycle</th>
              <th>Score</th>
              <th>Incident Type</th>
              <th>Details / Warning</th>
            </tr>
          </thead>
          <tbody>
            {incidents.map((incident) => (
              <tr key={incident.incident_id}>
                <td>
                  <strong>{formatDateTime(incident.end_time)}</strong>
                  {incident.start_time && incident.incident_kind === "data_availability" && (
                    <small>From {formatDateTime(incident.start_time)}</small>
                  )}
                </td>
                <td>{String(incident.shift || "–").replace(/^./, (value) => value.toUpperCase())}</td>
                <td>
                  <strong>{getFriendlySterilizerName(incident.sterilizer_name, incident.field)}</strong>
                  {incident.cycle_no && <small>Cycle {incident.cycle_no}</small>}
                </td>
                <td>
                  {incident.score === null || incident.score === undefined
                    ? "–"
                    : formatScore(incident.score)}
                  {incident.score_band && (
                    <small className={`incident-severity ${incident.score_band}`}>
                      {incident.score_band}
                    </small>
                  )}
                </td>
                <td>{(incident.incident_types || []).map((item) => item.replaceAll("_", " ")).join(" + ")}</td>
                <td>{(incident.details || []).join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="incident-appendix-footer">
        <span>Data Source: POM AI Platform</span>
        <span>Cycle assignment: Cycle end time</span>
        <span>Availability warning: {report.data_quality_policy?.data_availability_gap_minutes || 30}+ minutes</span>
        <span>Confidential</span>
      </footer>
    </section>
  );
}

function ReportContent({ report }) {
  const summary = report.summary;
  const goodOrBetter =
    (summary.bands?.excellent?.count || 0) + (summary.bands?.good?.count || 0);
  const sterilizerDescription = report.sterilizers.map((item) => item.sterilizer_name).join(" / ");
  const plantDisplayName = String(report.plant?.display_name || "").trim();
  const plantDataId = String(report.plant?.data_id || "").trim();
  const plantIdentity =
    plantDisplayName && plantDataId && plantDisplayName !== plantDataId
      ? `${plantDisplayName} (${plantDataId})`
      : plantDataId || plantDisplayName || "Plant";

  return (
    <article className="daily-report-document" id="daily-report-document">
      <section className="report-print-page report-single-page">
        <header className="daily-report-brand-header">
          <div className="daily-report-brand">
            <span className="daily-report-brand-icon">POM</span>
            <div>
              <h2>{report.report_title}</h2>
              <p>{plantIdentity} · {sterilizerDescription} Pressure Performance</p>
            </div>
          </div>
          <div className="daily-report-meta">
            <div><span>REPORT DATE</span><strong>{formatDate(`${report.report_date}T00:00:00`)}</strong></div>
            <div><span>SHIFT PERIOD</span><strong>{report.shift_comparison.morning.start} – {report.shift_comparison.night.end}</strong></div>
            <div><span>PAGE</span><strong>{report.incident_history?.length ? "1 · SUMMARY" : "1 of 1"}</strong></div>
          </div>
        </header>

        <div className="report-kpi-grid">
          <KpiCard
            label="TOTAL CYCLES"
            value={summary.total_cycles}
            suffix="cycles"
            tone="blue"
          />
          <KpiCard
            label="OVERALL CYCLE SCORE"
            value={formatScore(summary.average_score)}
            suffix="/100"
            tone="green"
          />
          <KpiCard
            label="GOOD OR EXCELLENT"
            value={goodOrBetter}
            suffix={percentage(goodOrBetter, summary.total_cycles)}
            tone="green"
          />
          <KpiCard
            label="FAIR CYCLES"
            value={summary.bands.fair.count}
            suffix={percentage(summary.bands.fair.count, summary.total_cycles)}
            tone="amber"
          />
          <KpiCard
            label="CRITICAL CYCLES"
            value={summary.bands.critical.count}
            suffix={percentage(summary.bands.critical.count, summary.total_cycles)}
            tone="red"
          />
        </div>

        <div className="report-two-column report-main-comparison-grid">
          <section className="report-panel report-performance-panel">
            <SectionHeader number="1" title="Sterilizer Performance Comparison (Cycle Score)" />
            <PerformanceTable sterilizers={report.sterilizers} />
          </section>
          <section className="report-panel report-distribution-panel">
            <SectionHeader number="2" title="Cycle Score Distribution (All)" />
            <DistributionChart summary={summary} />
            <PrintDistributionChart summary={summary} />
            <div className="report-inline-insight">
              <strong>Performance Summary</strong>
              <span>
                {percentage(goodOrBetter, summary.total_cycles)} of cycles achieved Good or Excellent performance. {percentage((summary.bands.critical.count || 0) + (summary.bands.poor.count || 0), summary.total_cycles)} require close attention.
              </span>
            </div>
          </section>
        </div>

        <section className="report-panel report-shift-section">
          <SectionHeader number="3" title="Shift Performance Comparison" />
          <div className="shift-section-grid">
            <div className="report-shift-chart-wrap">
              <ShiftChart comparison={report.shift_comparison} />
              <PrintShiftChart comparison={report.shift_comparison} />
            </div>
            <ShiftTable comparison={report.shift_comparison} />
          </div>
        </section>

        <section className="report-panel report-reliability-panel">
          <SectionHeader number="4" title="Reliability (Stability) Analysis" />
          <div className="report-table-wrap">
              <table className="report-table reliability-table">
                <thead>
                  <tr>
                    <th>Sterilizer</th><th>Avg Score</th><th>Std. Deviation</th><th>Score Range</th><th>CV</th><th>Rating</th>
                  </tr>
                </thead>
                <tbody>
                  {report.sterilizers.map((item) => (
                    <tr key={item.field}>
                      <th>{item.sterilizer_name}</th>
                      <td>{formatScore(item.reliability.average_score)}</td>
                      <td>{formatScore(item.reliability.standard_deviation)}</td>
                      <td>{item.reliability.score_range || "–"}</td>
                      <td>{item.reliability.cv_percent === null ? "–" : `${formatScore(item.reliability.cv_percent)}%`}</td>
                      <td><span className={`reliability-rating ${item.reliability.rating.toLowerCase()}`}>{item.reliability.rating}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
          </div>
        </section>

        <div className="report-two-column report-lower-grid">
          <section className="report-panel">
            <SectionHeader number="5" title="Stage Performance (Average Score by Stage)" />
            <div className="report-table-wrap">
              <table className="report-table stage-report-table">
                <thead>
                  <tr>
                    <th>Sterilizer</th><th>Total Cycles</th>
                    {["s1", "s2", "s3"].map((stage) => (
                      <th key={stage}>{stage.toUpperCase()}<small>Score / vs Benchmark</small></th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.sterilizers.map((item) => (
                    <tr key={item.field}>
                      <th>{item.sterilizer_name}</th>
                      <td>{item.total_cycles}</td>
                      {["s1", "s2", "s3"].map((stage) => {
                        const stageData = item.stage_performance[stage];
                        const deviation = stageData.average_deviation_bar;
                        return (
                          <td key={stage}>
                            <b>{formatScore(stageData.average_score)}</b>
                            <small>{deviation === null ? "–" : `${deviation > 0 ? "+" : ""}${Number(deviation).toFixed(3)} bar`}</small>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="report-panel report-insights-panel">
            <SectionHeader number="6" title="Daily Insights" />
            <div className="daily-insight-list">
              {report.insights.map((insight, index) => (
                <div className={`daily-insight-item insight-${insight.key}`} key={`${insight.key}-${index}`}>
                  <span>{index + 1}</span>
                  <div><strong>{insight.title}</strong><p>{insight.text}</p></div>
                </div>
              ))}
            </div>
            <div className={`report-incident-summary ${report.incident_history?.length ? "warning" : "clear"}`}>
              {report.incident_history?.length
                ? `${report.incident_history.length} reportable incident(s) recorded — see the full incident appendix.`
                : "No reportable incidents were recorded for this operational day."}
            </div>
            {report.warnings?.length > 0 && (
              <div className="report-warning-list no-print">
                <strong>Data Warnings</strong>
                {report.warnings.map((warning) => <span key={warning}>{warning}</span>)}
              </div>
            )}
          </section>
        </div>

        <footer className="report-page-footer">
          <span>Data Source: POM AI Platform</span>
          <span>Report Generated: {formatDateTime(report.generated_at)}</span>
          <span>Cycle assignment: Cycle end time</span>
          <span>Calculation unit: bar</span>
          <span>Confidential</span>
        </footer>
      </section>
      <IncidentHistoryAppendix report={report} />
    </article>
  );
}

export default function DailyReportPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [catalog, setCatalog] = usePageSessionState(
    "dailyReport",
    "catalog",
    [],
    { persist: false }
  );
  const [settingsConfig, setSettingsConfig] = usePageSessionState(
    "dailyReport",
    "settingsConfig",
    null,
    { persist: false }
  );
  const [selectedSiteCode, setSelectedSiteCode] = usePageSessionState(
    "dailyReport",
    "selectedSiteCode",
    ""
  );
  const [selectedPlantId, setSelectedPlantId] = usePageSessionState(
    "dailyReport",
    "selectedPlantId",
    ""
  );
  const [reportDate, setReportDate] = usePageSessionState(
    "dailyReport",
    "reportDate",
    defaultReportDate
  );
  const [report, setReport] = usePageSessionState(
    "dailyReport",
    "report",
    null,
    { persist: false }
  );
  const [loadingSettings, setLoadingSettings] = usePageSessionState(
    "dailyReport",
    "loadingSettings",
    true,
    { persist: false }
  );
  const [generating, setGenerating] = usePageSessionState(
    "dailyReport",
    "generating",
    false,
    { persist: false }
  );
  const [error, setError] = usePageSessionState(
    "dailyReport",
    "error",
    "",
    { persist: false }
  );

  useEffect(() => {
    const syncReportCharts = () => window.dispatchEvent(new Event("resize"));
    window.addEventListener("beforeprint", syncReportCharts);
    window.addEventListener("afterprint", syncReportCharts);
    return () => {
      window.removeEventListener("beforeprint", syncReportCharts);
      window.removeEventListener("afterprint", syncReportCharts);
    };
  }, []);

  useEffect(() => {
    const loadSites = async () => runLatestPageTask(
      "dailyReport:settings",
      async ({ isLatest }) => {
      try {
        setLoadingSettings(true);
        const response = await api.get("/api/daily-report/settings");
        if (!isLatest()) return;
        setSettingsConfig(response.data.settings || null);
        const available = (response.data.catalog || []).filter(
          (site) =>
            (site.plants || []).some(
              (plant) => (plant.sterilizers || []).length > 0
            )
        );
        setCatalog(available);
        if (available.length) {
          const restoredSite = available.find(
            (site) => site.site_code === selectedSiteCode
          );
          const nextSite = restoredSite || available[0];
          setSelectedSiteCode(nextSite.site_code);
          const selectablePlants = (nextSite.plants || []).filter(
            (plant) => (plant.sterilizers || []).length > 0
          );
          const restoredPlant = selectablePlants.find(
            (plant) => plant.data_id === selectedPlantId
          );
          setSelectedPlantId(
            restoredPlant?.data_id || selectablePlants[0]?.data_id || ""
          );
        } else {
          setSelectedSiteCode("");
          setSelectedPlantId("");
        }
        if (response.data.discovery_error) {
          console.error("Plant discovery warning:", response.data.discovery_error);
          setError("The plant list could not be fully refreshed. Existing saved configuration is still available.");
        }
      } catch (err) {
        if (isLatest()) {
          console.error("Daily Report configuration load error:", err);
          setError("Report configuration could not be loaded. Please try again.");
        }
      } finally {
        if (isLatest()) setLoadingSettings(false);
      }
    });
    loadSites();
  }, []);

  const selectedSiteInfo = useMemo(
    () => catalog.find((site) => site.site_code === selectedSiteCode),
    [catalog, selectedSiteCode]
  );

  const availablePlants = useMemo(
    () =>
      (selectedSiteInfo?.plants || []).filter(
        (plant) => (plant.sterilizers || []).length > 0
      ),
    [selectedSiteInfo]
  );

  const selectedPlantInfo = useMemo(
    () => availablePlants.find((plant) => plant.data_id === selectedPlantId) || null,
    [availablePlants, selectedPlantId]
  );

  const selectedSavedSite = useMemo(
    () =>
      (settingsConfig?.sites || []).find((site) => site.site_code === selectedSiteCode) || null,
    [settingsConfig, selectedSiteCode]
  );

  const shiftsConfigured = useMemo(() => {
    const morning = selectedSavedSite?.shifts?.morning;
    const night = selectedSavedSite?.shifts?.night;
    if (!morning?.start || !morning?.end || !night?.start || !night?.end) return false;
    if (morning.start === morning.end || night.start === night.end) return false;
    return morning.end === night.start && night.end === morning.start;
  }, [selectedSavedSite]);

  const activeForSelectedPlant = useMemo(
    () => settingsConfig?.active_benchmarks?.[selectedPlantId] || {},
    [settingsConfig, selectedPlantId]
  );

  const missingBenchmarkSterilizers = useMemo(
    () =>
      (selectedPlantInfo?.sterilizers || []).filter(
        (sterilizer) => !activeForSelectedPlant[sterilizer.field]
      ),
    [selectedPlantInfo, activeForSelectedPlant]
  );

  const reportConfigurationReady = Boolean(
    selectedSiteCode &&
      selectedPlantId &&
      selectedPlantInfo &&
      shiftsConfigured &&
      (selectedPlantInfo.sterilizers || []).length > 0 &&
      missingBenchmarkSterilizers.length === 0
  );

  const handleSiteChange = (siteCode) => {
    setSelectedSiteCode(siteCode);
    const nextSite = catalog.find((site) => site.site_code === siteCode);
    const firstPlant = (nextSite?.plants || []).find(
      (plant) => (plant.sterilizers || []).length > 0
    );
    setSelectedPlantId(firstPlant?.data_id || "");
    setReport(null);
  };

  const handleGenerate = async () => {
    if (!selectedSiteCode) {
      setError("Please select a site.");
      return;
    }
    if (!selectedPlantId) {
      setError("Please select a plant.");
      return;
    }
    if (!reportDate) {
      setError("Please select a report date.");
      return;
    }
    if (!reportConfigurationReady) {
      setError("Complete the required Settings configuration before generating the report.");
      return;
    }
    return runLatestPageTask("dailyReport:generate", async ({ isLatest }) => {
    try {
      setGenerating(true);
      setError("");
      setReport(null);
      const response = await api.post("/api/daily-report/generate", {
        site_code: selectedSiteCode,
        data_id: selectedPlantId,
        report_date: reportDate,
        smooth_window: 9,
      });
      if (!isLatest()) return;
      setReport(response.data);
    } catch (err) {
      if (isLatest()) {
        console.error("Daily Report generation error:", err);
        if (err?.response?.status && err.response.status < 500) {
          setError(err.response.data?.detail || "The report could not be generated. Check the selected setup and try again.");
        } else {
          setError("The report could not be generated. Please try again.");
        }
      }
    } finally {
      if (isLatest()) setGenerating(false);
    }
    });
  };

  const handlePrint = () => {
    window.dispatchEvent(new Event("resize"));
    window.setTimeout(() => window.print(), 150);
  };

  return (
    <div className="app-shell daily-report-app-shell">
      <TopNav />
      <main className="app-main daily-report-page">
        <header className="dashboard-topbar compact-dashboard-topbar daily-report-controls no-print">
          <div className="dashboard-title-group">
            <div className="dashboard-title-row"><h1>Daily Report</h1></div>
          </div>
          <div className="daily-report-filter-row">
            <label>
              Site
              <select
                value={selectedSiteCode}
                onChange={(event) => handleSiteChange(event.target.value)}
                disabled={loadingSettings || generating}
              >
                {!catalog.length && <option value="">No sites available</option>}
                {catalog.map((site) => (
                  <option value={site.site_code} key={site.site_code}>
                    {site.display_name} ({site.site_code})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Plant
              <select
                value={selectedPlantId}
                onChange={(event) => {
                  setSelectedPlantId(event.target.value);
                  setReport(null);
                }}
                disabled={loadingSettings || generating || !selectedSiteCode}
              >
                {!availablePlants.length && (
                  <option value="">No plants available</option>
                )}
                {availablePlants.map((plant) => (
                  <option value={plant.data_id} key={plant.data_id}>
                    {plant.display_name} ({plant.data_id})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Report Date
              <PlantDatePicker
                id="daily-report-date"
                value={reportDate}
                onChange={(value) => {
                  setReportDate(value);
                  setReport(null);
                  setError("");
                }}
                disabled={generating}
              />
            </label>
            <button type="button" className="btn-primary" onClick={handleGenerate} disabled={generating || loadingSettings || !reportDate || !reportConfigurationReady}>
              {generating ? "Generating Report..." : "Generate Report"}
            </button>
            {report && (
              <button type="button" className="btn-secondary" onClick={handlePrint}>
                Print / Save PDF
              </button>
            )}
          </div>
        </header>

        {!loadingSettings && selectedPlantInfo && (
          <section className={`dash-card daily-report-readiness no-print ${reportConfigurationReady ? "ready" : "incomplete"}`}>
            <div className="daily-report-readiness-header">
              <div>
                <strong>Report Configuration</strong>
                <span>
                  {reportConfigurationReady
                    ? "Configuration ready for report generation."
                    : "Complete the required Settings configuration before generating the report."}
                </span>
              </div>
              {!reportConfigurationReady && user?.role !== "viewer" && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() =>
                    navigate("/settings", {
                      state: {
                        settingsFocus: {
                          data_id: selectedPlantId,
                        },
                      },
                    })
                  }
                >
                  Open Settings
                </button>
              )}
              {!reportConfigurationReady && user?.role === "viewer" && (
                <span className="viewer-configuration-note">Contact an Editor or Admin to complete the report configuration.</span>
              )}
            </div>

            <div className="daily-report-readiness-list">
              <span className={shiftsConfigured ? "ready" : "missing"}>
                {shiftsConfigured ? "✓" : "!"} Shift settings {shiftsConfigured ? "configured" : "required"}
              </span>
              {(selectedPlantInfo.sterilizers || []).map((sterilizer) => {
                const hasActive = Boolean(activeForSelectedPlant[sterilizer.field]);
                return (
                  <span key={sterilizer.field} className={hasActive ? "ready" : "missing"}>
                    {hasActive ? "✓" : "!"} {sterilizer.sterilizer_name || sterilizer.field}: {hasActive ? "active benchmark configured" : "active benchmark required"}
                  </span>
                );
              })}
            </div>
          </section>
        )}

        {error && <div className="error-box no-print">{error}</div>}
        {generating && (
          <section className="dash-card no-print">
            <div className="daily-report-loading"><span className="report-spinner" /><div><strong>Generating the plant report...</strong><p>This retrieves the selected operational day with boundary context, detects complete cycles, and compares every sterilizer with its active benchmark.</p></div></div>
          </section>
        )}
        {!report && !generating && (
          <section className="dash-card daily-report-empty no-print">
            <strong>Choose a site, plant, and report date</strong>
            <p>The report requires configured shift hours and an active benchmark for every sterilizer in the selected plant.</p>
          </section>
        )}
        {report && (
          <ReportRenderBoundary
            resetKey={report.generated_at || report.report_date}
            onReset={() => {
              setReport(null);
              setError("The previous report could not be displayed. Please generate it again.");
            }}
          >
            <ReportContent report={report} />
          </ReportRenderBoundary>
        )}
      </main>
    </div>
  );
}
