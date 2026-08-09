import React, { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import ReactECharts from "echarts-for-react";
import api from "../api";
import QueryForm from "../components/QueryForm";
import TopNav from "../components/TopNav";
import ContinuousComparisonChart from "../components/ContinuousComparisonChart";
import RcaFeedbackPanel from "../components/RcaFeedbackPanel";
import { getSterilizerDisplayName } from "../utils/sterilizerDisplay";

const SCORE_BANDS = [
  {
    key: "excellent",
    label: "Excellent",
    rangeLabel: "≥ 90",
    min: 90,
    max: Infinity,
    color: "#2563eb",
    needsRca: false,
  },
  {
    key: "good",
    label: "Good",
    rangeLabel: "75–89",
    min: 75,
    max: 90,
    color: "#22c55e",
    needsRca: false,
  },
  {
    key: "fair",
    label: "Fair",
    rangeLabel: "60–74",
    min: 60,
    max: 75,
    color: "#f59e0b",
    needsRca: true,
  },
  {
    key: "poor",
    label: "Poor",
    rangeLabel: "50–59",
    min: 50,
    max: 60,
    color: "#f97316",
    needsRca: true,
  },
  {
    key: "critical",
    label: "Critical",
    rangeLabel: "< 50",
    min: -Infinity,
    max: 50,
    color: "#ef4444",
    needsRca: true,
  },
];

const STAT_SCOPES = [
  {
    key: "cycle",
    title: "Cycle Data Statistics",
    avgLabel: "Cycle Average",
    stageFocus: null,
  },
  {
    key: "s1",
    title: "Stage 1 Data Statistics",
    avgLabel: "Stage 1 Average",
    stageFocus: "S1",
  },
  {
    key: "s2",
    title: "Stage 2 Data Statistics",
    avgLabel: "Stage 2 Average",
    stageFocus: "S2",
  },
  {
    key: "s3",
    title: "Stage 3 Data Statistics",
    avgLabel: "Stage 3 Average",
    stageFocus: "S3",
  },
];

const MAX_DETAIL_CYCLES = 120;

function getScopeByKey(scopeKey) {
  return STAT_SCOPES.find((scope) => scope.key === scopeKey) || STAT_SCOPES[0];
}

function normaliseUnit(unit) {
  const value = String(unit || "bar").toLowerCase().trim();
  return value === "psi" ? "psi" : "bar";
}

function getYAxisMax(unit) {
  return normaliseUnit(unit) === "psi" ? 50 : 4;
}

function getCycleScore(cycle, scopeKey) {
  if (!cycle) return 0;

  if (scopeKey === "cycle") {
    return Number(cycle.score ?? 0);
  }

  const scoreBreakdown = cycle.score_breakdown || {};
  return Number(scoreBreakdown[`${scopeKey}_score`] ?? 0);
}

function getScoreBand(score) {
  const value = Number(score);

  return (
    SCORE_BANDS.find((band) => value >= band.min && value < band.max) ||
    SCORE_BANDS[SCORE_BANDS.length - 1]
  );
}

function calculateAverage(values) {
  const cleanValues = values
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));

  if (!cleanValues.length) return 0;

  return cleanValues.reduce((sum, value) => sum + value, 0) / cleanValues.length;
}

function buildStatistics(cycleResults, scope) {
  const scores = cycleResults.map((cycle) => getCycleScore(cycle, scope.key));
  const average = calculateAverage(scores);

  const bandRows = SCORE_BANDS.map((band) => {
    const cycles = cycleResults.filter((cycle) => {
      const score = getCycleScore(cycle, scope.key);
      return score >= band.min && score < band.max;
    });

    const percentage = cycleResults.length
      ? (cycles.length / cycleResults.length) * 100
      : 0;

    return {
      ...band,
      count: cycles.length,
      percentage,
      cycles,
    };
  });

  return {
    average,
    total: cycleResults.length,
    bandRows,
  };
}

function getStatusClass(score) {
  const value = Number(score);

  if (value >= 75) return "normal";
  if (value >= 60) return "warning";
  return "abnormal";
}

function getScoreColor(score) {
  return getScoreBand(score).color;
}

function formatDateTime(value) {
  if (!value) return "-";

  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function buildStagePatternForRca(cycle, stageFocus) {
  if (!stageFocus) {
    return cycle?.metrics?.pattern || null;
  }

  const patterns = cycle?.metrics?.patterns_detected;

  if (!Array.isArray(patterns)) {
    return cycle?.metrics?.pattern || null;
  }

  const stagePatterns = patterns
    .filter((item) => String(item.stage || "").toUpperCase() === stageFocus)
    .map((item) => item.pattern)
    .filter(Boolean);

  if (!stagePatterns.length) {
    return cycle?.metrics?.pattern || null;
  }

  return [...new Set(stagePatterns)].join("+");
}

function buildScoringResultForRca(cycle, scope, formData) {
  const scoreBreakdown = cycle.score_breakdown || {};
  const metrics = {
    ...(cycle.metrics || {}),

    // Context required by backend evidence_service.py
    bucket: formData.bucket,
    measurement: formData.measurement,
    tag_id: formData.tag_id,
    field: formData.field,
    source_unit: formData.source_unit,
    smooth_window: formData.smooth_window,
    cycle_no: cycle.cycle_no,
    cycle_start: cycle.cycle_start,
    cycle_end: cycle.cycle_end,
  };

  if (scope.stageFocus) {
    const stageLower = scope.stageFocus.toLowerCase();

    metrics.affected_stage = scope.stageFocus;
    metrics.user_selected_stage = scope.stageFocus;
    metrics.affected_stage_score = Number(
      scoreBreakdown[`${stageLower}_score`] ?? metrics.affected_stage_score ?? 0
    );
    metrics.pattern = buildStagePatternForRca(cycle, scope.stageFocus);
    metrics.rag_query_hint = `${scope.stageFocus} selected stage RCA review`;
  } else {
    // Overall cycle view: do not set user_selected_stage.
    // This lets the backend generate cycle-focused feedback.
    metrics.user_selected_stage = null;
  }

  return {
    score: Number(cycle.score ?? 0),
    classification: cycle.classification,
    score_band: cycle.score_band,
    score_breakdown: scoreBreakdown,

    // Top-level context fallback for backend evidence collection.
    cycle_no: cycle.cycle_no,
    cycle_start: cycle.cycle_start,
    cycle_end: cycle.cycle_end,
    duration_seconds: cycle.duration_seconds,
    bucket: formData.bucket,
    measurement: formData.measurement,
    tag_id: formData.tag_id,
    field: formData.field,
    source_unit: formData.source_unit,
    smooth_window: formData.smooth_window,

    data_context: {
      bucket: formData.bucket,
      measurement: formData.measurement,
      tag_id: formData.tag_id,
      field: formData.field,
      source_unit: formData.source_unit,
      smooth_window: formData.smooth_window,
      cycle_no: cycle.cycle_no,
      cycle_start: cycle.cycle_start,
      cycle_end: cycle.cycle_end,
    },

    metrics,
  };
}



function getRcaCacheKey(cycle, scope) {
  const cycleNo = cycle?.cycle_no ?? "unknown";
  return `${scope.key}_${cycleNo}`;
}

function MiniCycleChart({ cycle, unit }) {
  const data = cycle?.visual_overlay_chart || [];
  const cleanUnit = normaliseUnit(unit);
  const yMax = getYAxisMax(cleanUnit);

  if (!data.length) {
    return <div className="empty-state">No chart data.</div>;
  }

  const option = {
    animation: false,
    grid: {
      left: 54,
      right: 24,
      top: 34,
      bottom: 44,
      containLabel: false,
    },
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const item = params?.[0];
        if (!item) return "";

        const time = new Date(item.axisValue).toLocaleString();
        const lines = params
          .map((p) => {
            const value = Number(p.data?.[1]);
            return `${p.marker}${p.seriesName}: ${
              Number.isFinite(value) ? value.toFixed(2) : "-"
            } ${cleanUnit}`;
          })
          .join("<br/>");

        return `${time}<br/>${lines}`;
      },
    },
    legend: {
      bottom: 0,
      left: "center",
      textStyle: {
        color: "#64748b",
        fontSize: 12,
      },
    },
    xAxis: {
      type: "time",
      axisLabel: {
        color: "#64748b",
        fontSize: 11,
        hideOverlap: true,
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#cbd5e1" } },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: yMax,
      axisLabel: {
        color: "#64748b",
        fontSize: 11,
      },
      splitLine: {
        lineStyle: {
          color: "#e2e8f0",
          type: "dashed",
        },
      },
    },
    series: [
      {
        name: "Real-Time",
        type: "line",
        showSymbol: false,
        data: data.map((item) => [item.time, item.realtime_smooth]),
        lineStyle: {
          width: 2,
          color: "#2563eb",
        },
      },
      {
        name: "Benchmark",
        type: "line",
        showSymbol: false,
        data: data.map((item) => [
          item.time,
          item.benchmark ?? item.benchmark_overlay,
        ]),
        lineStyle: {
          width: 2,
          type: "dashed",
          color: "#22c55e",
        },
      },
    ],
  };

  return (
    <ReactECharts
      option={option}
      style={{
        width: "100%",
        height: 300,
      }}
      notMerge
      lazyUpdate
    />
  );
}

function StatisticsCard({ scope, statistics, onOpenDetail }) {
  return (
    <div className="analysis-stat-card compact-stat-card">
      <div className="analysis-stat-header compact-stat-header">
        <div>
          <h3>{scope.title}</h3>
          <p>{scope.avgLabel}</p>
        </div>

        <div className="analysis-stat-average compact-stat-average">
          {statistics.average.toFixed(1)}
          <span>%</span>
        </div>
      </div>

      <div className="analysis-stacked-bar">
        {statistics.bandRows.map((row) => (
          <div
            key={row.key}
            title={`${row.label}: ${row.percentage.toFixed(1)}%`}
            style={{
              width: `${row.percentage}%`,
              background: row.color,
              minWidth: row.percentage > 0 ? 5 : 0,
            }}
          />
        ))}
      </div>

      <div className="analysis-band-list compact-band-list">
        {statistics.bandRows.map((row) => (
          <div key={row.key} className="analysis-band-row compact-band-row">
            <div className="analysis-band-left">
              <span
                className="analysis-band-dot"
                style={{ background: row.color }}
              />
              <div>
                <strong>{row.label}</strong>
                <small>{row.rangeLabel}</small>
              </div>
            </div>

            <div className="analysis-band-count" aria-label={`${row.label} count`}>
              {row.count}
            </div>

            <div className="analysis-band-percent" aria-label={`${row.label} percentage`}>
              {row.percentage.toFixed(1)}%
            </div>

            <button
              type="button"
              className="analysis-detail-btn"
              disabled={row.count === 0}
              onClick={() => onOpenDetail(scope, row)}
            >
              Detail
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function DetailModal({
  detailView,
  cycleResults,
  unit,
  rcaCache,
  rcaLoadingKey,
  rcaError,
  selectedDetailCycleKey,
  setSelectedDetailCycleKey,
  onClose,
  onGenerateRca,
  onSwitchDetailScope,
}) {
  if (!detailView) return null;

  const { scope, band } = detailView;

  const filteredCycles = cycleResults.filter((cycle) => {
    const score = getCycleScore(cycle, scope.key);
    return score >= band.min && score < band.max;
  });

  const visibleCycles = filteredCycles.slice(0, MAX_DETAIL_CYCLES);

  const selectedCycle =
    visibleCycles.find(
      (cycle) => getRcaCacheKey(cycle, scope) === selectedDetailCycleKey
    ) || visibleCycles[0];

  const selectedScore = selectedCycle
    ? getCycleScore(selectedCycle, scope.key)
    : 0;

  const selectedCacheKey = selectedCycle
    ? getRcaCacheKey(selectedCycle, scope)
    : "";

  const cachedRca = selectedCacheKey ? rcaCache[selectedCacheKey] : null;
  const isLoading = rcaLoadingKey === selectedCacheKey;
  const rcaAllowed = selectedCycle && selectedScore < 75;

  const scoreNavigationItems = selectedCycle
    ? [
        {
          key: "s1",
          label: "S1 Score",
          value: Number(selectedCycle.score_breakdown?.s1_score ?? 0),
        },
        {
          key: "s2",
          label: "S2 Score",
          value: Number(selectedCycle.score_breakdown?.s2_score ?? 0),
        },
        {
          key: "s3",
          label: "S3 Score",
          value: Number(selectedCycle.score_breakdown?.s3_score ?? 0),
        },
        {
          key: "cycle",
          label: "Overall Score",
          value: Number(selectedCycle.score ?? 0),
        },
      ]
    : [];

  return (
    <div className="analysis-modal-backdrop">
      <div className="analysis-modal analysis-modal-wide">
        <div className="analysis-modal-header">
          <div>
            <h2>
              {scope.title} — {band.label} ({band.rangeLabel})
            </h2>
            <p>
              Showing {visibleCycles.length} of {filteredCycles.length} cycles.
              Select one cycle to view details. Click S1/S2/S3/Overall score
              cards to switch category for the same cycle.
            </p>
          </div>

          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        {rcaError && (
          <div className="error-box analysis-modal-error">{rcaError}</div>
        )}

        <div className="analysis-split-modal-body">
          <aside className="analysis-cycle-list-panel">
            <div className="analysis-cycle-list-title">Cycles</div>

            {!visibleCycles.length ? (
              <div className="empty-state">No cycles in this category.</div>
            ) : (
              <div className="analysis-cycle-list">
                {visibleCycles.map((cycle) => {
                  const score = getCycleScore(cycle, scope.key);
                  const cacheKey = getRcaCacheKey(cycle, scope);
                  const active = selectedCycle && cacheKey === selectedCacheKey;

                  return (
                    <button
                      key={cacheKey}
                      type="button"
                      className={`analysis-cycle-list-item ${
                        active ? "active" : ""
                      }`}
                      onClick={() => setSelectedDetailCycleKey(cacheKey)}
                    >
                      <div>
                        <strong>Cycle {cycle.cycle_no}</strong>
                        <small>{formatDateTime(cycle.cycle_start)}</small>
                      </div>

                      <span
                        style={{
                          color: getScoreColor(score),
                          background: `${getScoreColor(score)}18`,
                        }}
                      >
                        {score.toFixed(1)}%
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </aside>

          <section className="analysis-selected-cycle-panel">
            {!selectedCycle ? (
              <div className="empty-state">Select a cycle to view detail.</div>
            ) : (
              <>
                <div className="analysis-selected-chart-card compact-cycle-chart-card">
                  <div className="analysis-chart-card-header">
                    <div>
                      <h3>Cycle {selectedCycle.cycle_no}</h3>
                      <p>
                        {formatDateTime(selectedCycle.cycle_start)} →{" "}
                        {formatDateTime(selectedCycle.cycle_end)}
                      </p>
                    </div>

                    <div
                      className={`score-badge ${getStatusClass(selectedScore)}`}
                      style={{
                        background: `${getScoreColor(selectedScore)}22`,
                        color: getScoreColor(selectedScore),
                      }}
                    >
                      {selectedScore.toFixed(1)}%
                    </div>
                  </div>

                  <MiniCycleChart cycle={selectedCycle} unit={unit} />
                </div>

                <div className="analysis-score-navigation-note">
                  Click a score card below to switch the same cycle to another
                  detail category.
                </div>

                <div className="analysis-mini-score-grid analysis-mini-score-grid-wide analysis-clickable-score-grid">
                  {scoreNavigationItems.map((item) => {
                    const isActive = item.key === scope.key;
                    const bandInfo = getScoreBand(item.value);

                    return (
                      <button
                        key={item.key}
                        type="button"
                        className={`analysis-score-nav-card ${
                          isActive ? "active" : ""
                        }`}
                        onClick={() =>
                          onSwitchDetailScope(selectedCycle, item.key)
                        }
                      >
                        <span>{item.label}</span>
                        <strong style={{ color: bandInfo.color }}>
                          {item.value.toFixed(1)}%
                        </strong>
                        <small>{bandInfo.label}</small>
                      </button>
                    );
                  })}

                  <div className="analysis-score-nav-card static">
                    <span>Duration</span>
                    <strong>
                      {Number(selectedCycle.duration_seconds ?? 0).toFixed(1)}s
                    </strong>
                    <small>Cycle length</small>
                  </div>
                </div>

                <div className="analysis-rca-panel">
                  <div className="analysis-rca-panel-top">
                    <div>
                      <h3>AI RCA Feedback</h3>
                      <p>
                        RCA is generated on demand for the selected abnormal
                        cycle or stage.
                      </p>
                    </div>

                    {rcaAllowed ? (
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={isLoading}
                        onClick={() => onGenerateRca(selectedCycle, scope)}
                      >
                        {isLoading
                          ? "Generating RCA..."
                          : `Generate RCA for ${scope.stageFocus || "Cycle"}`}
                      </button>
                    ) : (
                      <span className="analysis-rca-skip-badge">
                        RCA skipped: selected score ≥ 75%
                      </span>
                    )}
                  </div>

                  {cachedRca ? (
                    <RcaFeedbackPanel rcaFeedback={cachedRca} />
                  ) : (
                    <div className="analysis-rca-empty">
                      {rcaAllowed
                        ? "Click Generate RCA to retrieve related RCA rules and recommendations."
                        : "This cycle is within the acceptable band for the selected category."}
                    </div>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function ComparisonParameterDrawer({
  open,
  onClose,
  formData,
  setFormData,
  onAnalyze,
  loading,
}) {
  return (
    <div className={`parameter-drawer-backdrop ${open ? "open" : ""}`}>
      <div className="parameter-drawer-panel">
        <div className="parameter-drawer-header">
          <div>
            <h2>Analysis Parameters</h2>
            <p>Set the comparison window and source channel.</p>
          </div>

          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="parameter-drawer-body">
          <QueryForm
            formData={formData}
            setFormData={setFormData}
            onDetect={onAnalyze}
            loading={loading}
            submitLabel="Analyze Cycles"
          />

          <div className="info-banner">
            V4 score bands: <strong>≥90</strong> Excellent,{" "}
            <strong>75–89</strong> Good, <strong>60–74</strong> Fair,{" "}
            <strong>50–59</strong> Poor, <strong>&lt;50</strong> Critical.
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ComparisonPage() {
  const location = useLocation();
  const [parameterOpen, setParameterOpen] = useState(false);

  const [formData, setFormData] = useState({
    bucket: "Mill",
    measurement: "PSTR",
    field: "ch4",
    tag_id: "",
    source_unit: "bar",
    start_time: "",
    stop_time: "",
    smooth_window: 9,
  });

  const [selectedBenchmarkFile] = useState(
    location.state?.selectedBenchmarkFile ||
      localStorage.getItem("selectedBenchmarkFile") ||
      ""
  );

  const [loadedBenchmark, setLoadedBenchmark] = useState(() => {
    if (location.state?.loadedBenchmark) {
      return location.state.loadedBenchmark;
    }

    const saved = localStorage.getItem("loadedBenchmark");
    return saved ? JSON.parse(saved) : null;
  });

  const [comparisonResult, setComparisonResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [benchmarkError, setBenchmarkError] = useState("");
  const [error, setError] = useState("");

  const [detailView, setDetailView] = useState(null);
  const [selectedDetailCycleKey, setSelectedDetailCycleKey] = useState("");
  const [rcaCache, setRcaCache] = useState({});
  const [rcaLoadingKey, setRcaLoadingKey] = useState("");
  const [rcaError, setRcaError] = useState("");

  useEffect(() => {
    const loadBenchmarkIfNeeded = async () => {
      if (!loadedBenchmark && selectedBenchmarkFile) {
        try {
          const res = await api.get(`/api/benchmarks/${selectedBenchmarkFile}`);
          setLoadedBenchmark(res.data.benchmark);
          localStorage.setItem(
            "loadedBenchmark",
            JSON.stringify(res.data.benchmark)
          );
        } catch (err) {
          setBenchmarkError(err?.response?.data?.detail || err.message);
        }
      }
    };

    loadBenchmarkIfNeeded();
  }, [loadedBenchmark, selectedBenchmarkFile]);

  const validateQueryForm = () => {
    if (!formData.bucket?.trim()) return "Please enter Bucket.";
    if (!formData.measurement?.trim()) return "Please enter Measurement.";
    if (!formData.field?.trim()) return "Please enter Field / Channel.";
    if (!formData.tag_id?.trim()) return "Please enter Tag ID.";
    if (!formData.source_unit?.trim()) return "Please choose Source Unit.";
    if (!formData.start_time?.trim()) return "Please enter Start Time.";
    if (!formData.stop_time?.trim()) return "Please enter Stop Time.";

    if (
      new Date(formData.start_time).getTime() >=
      new Date(formData.stop_time).getTime()
    ) {
      return "Stop Time must be later than Start Time.";
    }

    if (
      !Number.isFinite(Number(formData.smooth_window)) ||
      Number(formData.smooth_window) < 1
    ) {
      return "Smooth Window must be a number greater than 0.";
    }

    return "";
  };

  const getCompareErrorMessage = (err) => {
    console.error("Compare cycles error:", err);

    if (err?.code === "ECONNABORTED") {
      return "Connection timeout: backend took too long to respond. Try a shorter time range first.";
    }

    if (err?.response) {
      return (
        err.response.data?.detail ||
        `Backend error ${err.response.status}: ${err.response.statusText}`
      );
    }

    if (err?.request) {
      return "Connection Error: frontend cannot reach FastAPI. Check that backend is running on http://127.0.0.1:8000.";
    }

    return err?.message || "Unknown frontend error.";
  };

  const getRcaErrorMessage = (err) => {
    console.error("RCA feedback error:", err);

    if (err?.code === "ECONNABORTED") {
      return "RCA timeout: backend took too long to generate RCA feedback. Set ENABLE_RERANKER=false for faster local CPU testing.";
    }

    if (err?.response) {
      return (
        err.response.data?.detail ||
        `RCA backend error ${err.response.status}: ${err.response.statusText}`
      );
    }

    if (err?.request) {
      return "RCA connection error: frontend cannot reach FastAPI.";
    }

    return err?.message || "Unknown RCA error.";
  };

  const handleRunAnalysis = async () => {
    const validationError = validateQueryForm();
    if (validationError) {
      setError(validationError);
      return;
    }

    if (!selectedBenchmarkFile) {
      setError("Please select a benchmark first.");
      return;
    }

    try {
      setLoading(true);
      setError("");
      setRcaError("");
      setComparisonResult(null);
      setDetailView(null);
      setSelectedDetailCycleKey("");
      setRcaCache({});

      const res = await api.post("/api/compare-cycles", {
        ...formData,
        benchmark_file_name: selectedBenchmarkFile,
      });

      setComparisonResult(res.data);
      setParameterOpen(false);
    } catch (err) {
      setError(getCompareErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const handleOpenCycleFromChart = (cycle) => {
    if (!cycle) return;

    const scope = getScopeByKey("cycle");
    const score = getCycleScore(cycle, "cycle");
    const band = getScoreBand(score);

    setRcaError("");
    setDetailView({
      scope,
      band,
    });

    setSelectedDetailCycleKey(`cycle_${cycle.cycle_no ?? "unknown"}`);
  };

  const handleOpenDetail = (scope, band) => {
    setRcaError("");
    setSelectedDetailCycleKey("");
    setDetailView({ scope, band });
  };

  const handleSwitchDetailScope = (cycle, targetScopeKey) => {
    const targetScope = getScopeByKey(targetScopeKey);
    const targetScore = getCycleScore(cycle, targetScope.key);
    const targetBand = getScoreBand(targetScore);

    setRcaError("");
    setDetailView({
      scope: targetScope,
      band: targetBand,
    });

    setSelectedDetailCycleKey(`${targetScope.key}_${cycle.cycle_no ?? "unknown"}`);
  };

  const handleGenerateRca = async (cycle, scope) => {
    const cacheKey = getRcaCacheKey(cycle, scope);

    if (rcaCache[cacheKey]) {
      return;
    }

    try {
      setRcaLoadingKey(cacheKey);
      setRcaError("");

      const scoringResult = buildScoringResultForRca(cycle, scope, formData);

      const res = await api.post("/rag/feedback", {
        scoring_result: scoringResult,
        peer_confirmation_available: false,
      });

      setRcaCache((prev) => ({
        ...prev,
        [cacheKey]: res.data,
      }));
    } catch (err) {
      setRcaError(getRcaErrorMessage(err));
    } finally {
      setRcaLoadingKey("");
    }
  };

  const cycleResults = comparisonResult?.cycle_results || [];

  const statisticsByScope = useMemo(() => {
    const stats = {};

    for (const scope of STAT_SCOPES) {
      stats[scope.key] = buildStatistics(cycleResults, scope);
    }

    return stats;
  }, [cycleResults]);

  const chartUnit =
    comparisonResult?.benchmark_unit ||
    loadedBenchmark?.benchmark_unit ||
    loadedBenchmark?.unit ||
    formData.source_unit ||
    "bar";

  const selectedSterilizerLabel = formData.tag_id
    ? getSterilizerDisplayName(formData.tag_id, formData.field)
    : "";

  return (
    <div className="app-shell">
      <TopNav />

      <main className="app-main">
        <header className="dashboard-topbar compact-dashboard-topbar">
          <div className="dashboard-title-group">
            <div className="dashboard-title-row">
              <h1>AI Comparison and Analysis</h1>

              {selectedSterilizerLabel && (
                <span className="dashboard-sterilizer-pill">
                  {selectedSterilizerLabel}
                </span>
              )}
            </div>

            <p>
              Compare sterilizer cycles against the selected benchmark and run
              RCA on demand.
            </p>
          </div>

          <div className="dashboard-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setParameterOpen(true)}
            >
              Parameters
            </button>

            <button
              type="button"
              className="btn-primary"
              disabled={loading}
              onClick={handleRunAnalysis}
            >
              {loading ? "Analyzing..." : "Analyze"}
            </button>
          </div>
        </header>

        {!selectedBenchmarkFile && (
          <div className="error-box">
            No benchmark selected. Please return to Benchmark Management and
            load one first.
          </div>
        )}

        {benchmarkError && <div className="error-box">{benchmarkError}</div>}
        {error && <div className="error-box">{error}</div>}

        <section className="dash-card dashboard-card-large">
          <div className="dash-card-title">
            Continuous Signal & Benchmark Overlay
          </div>

          <div className="chart-panel-body">
            {loading ? (
              <div className="empty-state">
                Calculating cycle scores and stage statistics...
              </div>
            ) : comparisonResult ? (
              <ContinuousComparisonChart
                comparisonResult={comparisonResult}
                unit={chartUnit}
                onCycleClick={handleOpenCycleFromChart}
              />
            ) : (
              <div className="empty-state">
                Open Parameters, choose the time window, and run analysis to
                view the overlay chart.
              </div>
            )}
          </div>
        </section>

        {comparisonResult && (
          <section className="analysis-stat-grid four-stat-grid">
            {STAT_SCOPES.map((scope) => (
              <StatisticsCard
                key={scope.key}
                scope={scope}
                statistics={statisticsByScope[scope.key]}
                onOpenDetail={handleOpenDetail}
              />
            ))}
          </section>
        )}

        <DetailModal
          detailView={detailView}
          cycleResults={cycleResults}
          unit={chartUnit}
          rcaCache={rcaCache}
          rcaLoadingKey={rcaLoadingKey}
          rcaError={rcaError}
          selectedDetailCycleKey={selectedDetailCycleKey}
          setSelectedDetailCycleKey={setSelectedDetailCycleKey}
          onClose={() => setDetailView(null)}
          onGenerateRca={handleGenerateRca}
          onSwitchDetailScope={handleSwitchDetailScope}
        />

        <ComparisonParameterDrawer
          open={parameterOpen}
          onClose={() => setParameterOpen(false)}
          formData={formData}
          setFormData={setFormData}
          onAnalyze={handleRunAnalysis}
          loading={loading}
        />
      </main>
    </div>
  );
}