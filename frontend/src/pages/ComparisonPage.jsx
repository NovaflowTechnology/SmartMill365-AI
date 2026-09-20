import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useNavigate } from "react-router-dom";
import api from "../api";
import { useAuth } from "../auth/AuthContext";
import QueryForm from "../components/QueryForm";
import TopNav from "../components/TopNav";
import ContinuousComparisonChart from "../components/ContinuousComparisonChart";
import RcaFeedbackPanel from "../components/RcaFeedbackPanel";
import { getSterilizerDisplayName } from "../utils/sterilizerDisplay";
import { formatDurationHoursMinutes } from "../utils/duration";
import {
  formatPlantDateTime,
  isValidPlantDateTimeRange,
  toPlantRequestDateTime,
} from "../utils/plantTime";
import {
  runLatestPageTask,
  runPageTask,
  usePageSessionState,
} from "../state/pageSessionStore";

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

function comparisonQueryKey(formData) {
  return JSON.stringify({
    bucket: formData.bucket || "",
    field: formData.field || "",
    tag_id: formData.tag_id || "",
    source_unit: formData.source_unit || "bar",
    start_time: toPlantRequestDateTime(formData.start_time) || formData.start_time || "",
    stop_time: toPlantRequestDateTime(formData.stop_time) || formData.stop_time || "",
    smooth_window: Number(formData.smooth_window || 0),
  });
}

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
  return value ? formatPlantDateTime(value) : "-";
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

        const time = formatPlantDateTime(item.axisValue);
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
              flex: `0 0 ${row.percentage}%`,
              background: row.color,
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
  useEffect(() => {
    if (!detailView) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [detailView, onClose]);

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
    <div
      className="analysis-modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="analysis-modal analysis-modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Cycle Analysis Feedback"
      >
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
                      {formatDurationHoursMinutes(selectedCycle.duration_seconds)}
                    </strong>
                    <small>Cycle length</small>
                  </div>
                </div>

                <div className="analysis-rca-panel">
                  <div className="analysis-rca-panel-top">
                    <div>
                      <h3>AI Analysis Feedback</h3>
                      <p>
                        Analysis is generated on demand for the selected abnormal
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
                          ? "Generating Analysis..."
                          : `Generate Analysis for ${scope.stageFocus || "Cycle"}`}
                      </button>
                    ) : (
                      <span className="analysis-rca-skip-badge">
                        AI Analysis is not required because the selected score is 75% or above.
                      </span>
                    )}
                  </div>

                  {cachedRca ? (
                    <RcaFeedbackPanel rcaFeedback={cachedRca} />
                  ) : (
                    <div className="analysis-rca-empty">
                      {rcaAllowed
                        ? "Click Generate Analysis to retrieve related analysis rules and recommendations."
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
  useEffect(() => {
    if (!open) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  return (
    <div
      className={`parameter-drawer-backdrop ${open ? "open" : ""}`}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="parameter-drawer-panel" role="dialog" aria-modal="true" aria-label="Analysis Setup">
        <div className="parameter-drawer-header">
          <div>
            <h2>Analysis Setup</h2>
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
            submitLabel="Run Analysis"
          />
        </div>
      </div>
    </div>
  );
}

export default function ComparisonPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [parameterOpen, setParameterOpen] = useState(false);

  const [formData, setFormData] = usePageSessionState(
    "comparison",
    "formData",
    {
      bucket: "",
      field: "",
      tag_id: "",
      plant_display_name: "",
      source_unit: "bar",
      start_time: "",
      stop_time: "",
      smooth_window: 9,
    }
  );

  const [selectedBenchmarkId, setSelectedBenchmarkId] = useState("");
  const [loadedBenchmark, setLoadedBenchmark] = useState(null);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);

  const [comparisonResult, setComparisonResult] = usePageSessionState(
    "comparison",
    "result",
    null,
    { persist: false }
  );
  const [resultBenchmarkId, setResultBenchmarkId] = usePageSessionState(
    "comparison",
    "resultBenchmarkId",
    "",
    { persist: false }
  );
  const [loading, setLoading] = usePageSessionState(
    "comparison",
    "loading",
    false,
    { persist: false }
  );
  const [benchmarkError, setBenchmarkError] = usePageSessionState(
    "comparison",
    "benchmarkError",
    "",
    { persist: false }
  );
  const [benchmarkMissing, setBenchmarkMissing] = useState(false);
  const [error, setError] = usePageSessionState(
    "comparison",
    "error",
    "",
    { persist: false }
  );

  const [detailView, setDetailView] = useState(null);
  const [selectedDetailCycleKey, setSelectedDetailCycleKey] = useState("");
  const [rcaCache, setRcaCache] = usePageSessionState(
    "comparison",
    "rcaCache",
    {}
  );
  const [rcaLoadingKey, setRcaLoadingKey] = usePageSessionState(
    "comparison",
    "rcaLoadingKey",
    "",
    { persist: false }
  );
  const [rcaError, setRcaError] = usePageSessionState(
    "comparison",
    "rcaError",
    "",
    { persist: false }
  );
  const formDataRef = useRef(formData);
  formDataRef.current = formData;
  const currentQueryKey = comparisonQueryKey(formData);
  const previousQueryKeyRef = useRef(currentQueryKey);

  useEffect(() => {
    if (previousQueryKeyRef.current === currentQueryKey) return;
    previousQueryKeyRef.current = currentQueryKey;
    setComparisonResult(null);
    setResultBenchmarkId("");
    setDetailView(null);
    setSelectedDetailCycleKey("");
    setRcaCache({});
    setRcaLoadingKey("");
    setRcaError("");
    setError("");
  }, [
    currentQueryKey,
    setComparisonResult,
    setResultBenchmarkId,
    setRcaCache,
    setRcaLoadingKey,
    setRcaError,
    setError,
  ]);

  useEffect(() => {
    let cancelled = false;

    const loadActiveBenchmark = async () => {
      const tagId = String(formData.tag_id || "").trim();
      const field = String(formData.field || "").trim();

      // Active benchmark selection belongs to Settings/Supabase. Benchmark
      // Management preview state must never determine comparison behavior.
      setSelectedBenchmarkId("");
      setLoadedBenchmark(null);
      setBenchmarkError("");
      setBenchmarkMissing(false);

      if (!tagId || !field) {
        setBenchmarkLoading(false);
        return;
      }

      try {
        setBenchmarkLoading(true);
        const res = await api.get(
          `/api/active-benchmark/${encodeURIComponent(tagId)}/${encodeURIComponent(field)}`
        );

        if (cancelled) return;

        const benchmark = res.data?.benchmark || null;
        const benchmarkId = String(
          benchmark?.benchmark_id || benchmark?.database_id || ""
        ).trim();
        if (!benchmark || !benchmarkId || !benchmark?.file_name) {
          throw new Error("Active benchmark response is incomplete.");
        }

        setLoadedBenchmark(benchmark);
        setSelectedBenchmarkId(benchmarkId);
      } catch (err) {
        if (cancelled) return;
        setSelectedBenchmarkId("");
        setLoadedBenchmark(null);
        if (err?.response?.status === 404) {
          setBenchmarkMissing(true);
          setBenchmarkError("");
        } else {
          setBenchmarkMissing(false);
          setBenchmarkError("The active benchmark could not be loaded. Please try again.");
        }
      } finally {
        if (!cancelled) setBenchmarkLoading(false);
      }
    };

    loadActiveBenchmark();

    return () => {
      cancelled = true;
    };
  }, [formData.tag_id, formData.field]);

  useEffect(() => {
    if (
      comparisonResult &&
      resultBenchmarkId &&
      resultBenchmarkId !== selectedBenchmarkId
    ) {
      setComparisonResult(null);
      setResultBenchmarkId("");
      setRcaCache({});
      setRcaError("");
    }
  }, [
    comparisonResult,
    resultBenchmarkId,
    selectedBenchmarkId,
    setComparisonResult,
    setResultBenchmarkId,
    setRcaCache,
    setRcaError,
  ]);

  const validateQueryForm = () => {
    if (!formData.field?.trim()) return "Please select a sterilizer.";
    if (!formData.tag_id?.trim()) return "Please select a Data ID.";
    if (!formData.source_unit?.trim()) return "Please choose Pressure Unit.";
    if (!formData.start_time?.trim()) return "Please enter Start Time.";
    if (!formData.stop_time?.trim()) return "Please enter Stop Time.";

    if (
      !toPlantRequestDateTime(formData.start_time) ||
      !toPlantRequestDateTime(formData.stop_time)
    ) {
      return "Please enter Start Time and Stop Time as DD/MM/YYYY HH:mm.";
    }

    if (
      !isValidPlantDateTimeRange(formData.start_time, formData.stop_time)
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
      return "The analysis did not complete in time. Try a shorter time range and run the analysis again.";
    }

    if (err?.response) {
      if (err.response.status >= 500) {
        return "The analysis service could not complete the request. Please try again.";
      }
      return err.response.data?.detail || "The analysis request could not be completed. Check the selected setup and try again.";
    }

    if (err?.request) {
      return "The analysis service could not be reached. Please try again.";
    }

    return "The analysis could not be completed. Please try again.";
  };

  const getRcaErrorMessage = (err) => {
    console.error("AI analysis feedback error:", err);

    if (err?.code === "ECONNABORTED") {
      return "AI Analysis did not complete in time. Please try again.";
    }

    if (err?.response) {
      if (err.response.status >= 500) {
        return "AI Analysis could not be generated. Please try again.";
      }
      return err.response.data?.detail || "AI Analysis could not be generated for the selected cycle. Please try again.";
    }

    if (err?.request) {
      return "The AI Analysis service could not be reached. Please try again.";
    }

    return "AI Analysis could not be generated. Please try again.";
  };

  const handleRunAnalysis = async () => {
    const validationError = validateQueryForm();
    if (validationError) {
      setError(validationError);
      return;
    }

    if (benchmarkLoading) {
      setError("Please wait while the active benchmark is loading.");
      return;
    }

    if (!selectedBenchmarkId) {
      if (!benchmarkError) setBenchmarkMissing(true);
      setError("");
      return;
    }

    const requestedFormData = {
      bucket: formData.bucket || "",
      field: formData.field || "",
      tag_id: formData.tag_id || "",
      source_unit: formData.source_unit || "bar",
      start_time: toPlantRequestDateTime(formData.start_time),
      stop_time: toPlantRequestDateTime(formData.stop_time),
      smooth_window: Number(formData.smooth_window || 9),
    };
    const requestedQueryKey = comparisonQueryKey(requestedFormData);

    return runLatestPageTask("comparison:analyze", async ({ isLatest }) => {
      try {
        setLoading(true);
        setError("");
        setRcaError("");
        setComparisonResult(null);
        setDetailView(null);
        setSelectedDetailCycleKey("");
        setRcaCache({});

        const res = await api.post("/api/compare-cycles", {
          ...requestedFormData,
        });

        if (
          !isLatest() ||
          comparisonQueryKey(formDataRef.current) !== requestedQueryKey
        ) return;
        const actualBenchmarkId =
          res.data?.benchmark_id || selectedBenchmarkId;
        setSelectedBenchmarkId(actualBenchmarkId);
        setComparisonResult(res.data);
        setResultBenchmarkId(actualBenchmarkId);
        setParameterOpen(false);
      } catch (err) {
        if (
          isLatest() &&
          comparisonQueryKey(formDataRef.current) === requestedQueryKey
        ) setError(getCompareErrorMessage(err));
      } finally {
        if (isLatest()) setLoading(false);
      }
    });
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

    return runPageTask(`comparison:rca:${cacheKey}`, async () => {
      try {
        setRcaLoadingKey(cacheKey);
        setRcaError("");

        const scoringResult = buildScoringResultForRca(cycle, scope, {
          ...formData,
          measurement: comparisonResult?.source_measurement || "",
        });

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
    });
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
    comparisonResult?.source_unit ||
    loadedBenchmark?.benchmark_unit ||
    loadedBenchmark?.unit ||
    formData.source_unit ||
    "bar";

  const selectedSterilizerLabel = formData.tag_id
    ? getSterilizerDisplayName(
        formData.tag_id,
        formData.field,
        formData.plant_display_name
      )
    : "";

  return (
    <div className="app-shell">
      <TopNav />

      <main className="app-main comparison-page">
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

          </div>

          <div className="dashboard-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setParameterOpen(true)}
            >
              Analysis Setup
            </button>

            <button
              type="button"
              className="btn-primary"
              disabled={loading || benchmarkLoading || !selectedBenchmarkId}
              onClick={handleRunAnalysis}
            >
              {loading
                ? "Running Analysis..."
                : benchmarkLoading
                  ? "Loading Benchmark..."
                  : "Run Analysis"}
            </button>
          </div>
        </header>

        {formData.tag_id &&
          formData.field &&
          !benchmarkLoading &&
          !selectedBenchmarkId &&
          benchmarkMissing && (
            <div className="error-box actionable-error-box">
              <div>
                <strong>No Active Benchmark</strong>
                <span>
                  The selected sterilizer does not have an active benchmark. Configure one before running the analysis.
                </span>
              </div>
              {user?.role !== "viewer" ? (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() =>
                    navigate("/settings", {
                      state: {
                        settingsFocus: {
                          data_id: formData.tag_id,
                          field: formData.field,
                        },
                      },
                    })
                  }
                >
                  Open Settings
                </button>
              ) : (
                <span className="viewer-configuration-note">Contact an Editor or Admin to configure an active benchmark.</span>
              )}
            </div>
          )}

        {benchmarkError && <div className="error-box">{benchmarkError}</div>}
        {error && <div className="error-box">{error}</div>}

        <section className="dash-card dashboard-card-large comparison-chart-card">
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
                Open Analysis Setup, choose the time window, and run analysis to
                view the overlay chart.
              </div>
            )}
          </div>
        </section>

        {comparisonResult && (
          <section className="analysis-stat-grid four-stat-grid comparison-stat-grid">
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
