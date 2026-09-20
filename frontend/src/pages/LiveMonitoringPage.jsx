import { useEffect, useRef, useState } from "react";
import api from "../api";
import TopNav from "../components/TopNav";
import SterilizerLiveCard from "../components/SterilizerLiveCard";
import PlantDateTimePicker from "../components/PlantDateTimePicker";
import {
  usePageSessionState,
} from "../state/pageSessionStore";
import {
  defaultPlantDateTime,
  formatPlantDateTime,
  isValidPlantDateTimeRange,
  toPlantRequestDateTime,
} from "../utils/plantTime";

function LiveParameterDrawer({
  open,
  onClose,
  tags,
  selectedTag,
  onTagChange,
  sourceUnit,
  setSourceUnit,
  rangeMode,
  setRangeMode,
  timeRangeHours,
  setTimeRangeHours,
  customStartTime,
  setCustomStartTime,
  customEndTime,
  setCustomEndTime,
  refreshSeconds,
  setRefreshSeconds,
  smoothWindow,
  setSmoothWindow,
  onRefresh,
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
      <div className="parameter-drawer-panel" role="dialog" aria-modal="true" aria-label="Monitoring Setup">
        <div className="parameter-drawer-header">
          <div>
            <h2>Monitoring Setup</h2>
          </div>
          <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
        </div>

        <div className="parameter-drawer-body">
          <div className="query-form-container">
            <div className="field-group">
              <label>Pressure Unit</label>
              <select value={sourceUnit} onChange={(e) => setSourceUnit(e.target.value)}>
                <option value="bar">bar</option>
                <option value="psi">psi</option>
              </select>
            </div>

            <div className="field-group">
              <label>Plant</label>
              <select value={selectedTag} onChange={(e) => onTagChange(e.target.value)}>
                <option value="">-- Select a Plant --</option>
                {tags.map((item) => (
                  <option key={item.tag_id} value={item.tag_id}>
                    {(item.display_name || item.tag_name) !== item.tag_id
                      ? `${item.display_name || item.tag_name} (${item.tag_id})`
                      : item.tag_id} — {item.sterilizer_count} sterilizers
                  </option>
                ))}
              </select>
            </div>

            <div className="field-group">
              <label>Range Type</label>
              <select value={rangeMode} onChange={(e) => setRangeMode(e.target.value)}>
                <option value="latest">Latest Period (Auto Refresh)</option>
                <option value="custom">Custom Start and End Date/Time</option>
              </select>
            </div>

            {rangeMode === "latest" && (
              <>
                <div className="field-group">
                  <label>Display Time Range</label>
                  <select value={timeRangeHours} onChange={(e) => setTimeRangeHours(Number(e.target.value))}>
                    <option value={3}>Latest 3 Hours</option>
                    <option value={6}>Latest 6 Hours</option>
                    <option value={12}>Latest 12 Hours</option>
                    <option value={24}>Latest 24 Hours</option>
                  </select>
                </div>

                <div className="field-group">
                  <label>Refresh Every (Seconds)</label>
                  <select value={refreshSeconds} onChange={(e) => setRefreshSeconds(Number(e.target.value))}>
                    <option value={5}>5</option>
                    <option value={10}>10</option>
                    <option value={15}>15</option>
                    <option value={30}>30</option>
                  </select>
                </div>
              </>
            )}

            <div className={`live-custom-range-grid ${rangeMode === "custom" ? "active" : ""}`}>
              <div className="live-custom-range-header">
                <strong>Specific Date and Time Range</strong>
                <span>
                  Enter the exact period containing the cycles you want to view.
                  Editing either field automatically selects the custom range.
                </span>
              </div>

              <div className="field-group">
                <label>Start Date and Time</label>
                <PlantDateTimePicker
                  id="live-custom-start"
                  value={customStartTime}
                  onChange={(value) => {
                    setRangeMode("custom");
                    setCustomStartTime(value);
                  }}
                />
              </div>

              <div className="field-group">
                <label>End Date and Time</label>
                <PlantDateTimePicker
                  id="live-custom-end"
                  value={customEndTime}
                  onChange={(value) => {
                    setRangeMode("custom");
                    setCustomEndTime(value);
                  }}
                />
              </div>
            </div>

            <div className="field-group">
              <label>Smooth Window</label>
              <input type="number" min="1" value={smoothWindow} onChange={(e) => setSmoothWindow(Number(e.target.value))} />
            </div>

            <div className="button-row left-align">
              <button type="button" onClick={onRefresh} disabled={loading || !selectedTag}>
                {loading
                  ? "Loading..."
                  : rangeMode === "custom"
                    ? "View Selected Range"
                    : "Refresh Now"}
              </button>
            </div>

            <div className="live-monitor-note">
              {rangeMode === "custom"
                ? "Custom ranges are loaded when you select View Selected Range. They do not auto-refresh because the chosen historical window is fixed."
                : `The chart shows the latest ${timeRangeHours} hours and refreshes automatically every ${refreshSeconds} seconds. To view an exact period, edit the Start Date and Time or End Date and Time above.`}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LiveMonitoringPage() {
  const [parameterOpen, setParameterOpen] = useState(false);
  const [tags, setTags] = usePageSessionState(
    "liveMonitoring",
    "tags",
    [],
    { persist: false }
  );
  const [selectedTag, setSelectedTag] = usePageSessionState(
    "liveMonitoring",
    "selectedTag",
    ""
  );
  const [sourceUnit, setSourceUnit] = usePageSessionState(
    "liveMonitoring",
    "sourceUnit",
    "bar"
  );
  const [rangeMode, setRangeMode] = usePageSessionState(
    "liveMonitoring",
    "rangeMode",
    "latest"
  );
  const [timeRangeHours, setTimeRangeHours] = usePageSessionState(
    "liveMonitoring",
    "timeRangeHours",
    3
  );
  const [customStartTime, setCustomStartTime] = usePageSessionState(
    "liveMonitoring",
    "customStartTime",
    () => defaultPlantDateTime(3)
  );
  const [customEndTime, setCustomEndTime] = usePageSessionState(
    "liveMonitoring",
    "customEndTime",
    () => defaultPlantDateTime(0)
  );
  const [refreshSeconds, setRefreshSeconds] = usePageSessionState(
    "liveMonitoring",
    "refreshSeconds",
    15
  );
  const [smoothWindow, setSmoothWindow] = usePageSessionState(
    "liveMonitoring",
    "smoothWindow",
    9
  );
  const [loading, setLoading] = usePageSessionState(
    "liveMonitoring",
    "loading",
    false,
    { persist: false }
  );
  const [liveResult, setLiveResult] = usePageSessionState(
    "liveMonitoring",
    "result",
    null,
    { persist: false }
  );
  const [error, setError] = usePageSessionState(
    "liveMonitoring",
    "error",
    "",
    { persist: false }
  );

  const timerRef = useRef(null);
  const abortControllerRef = useRef(null);
  const requestInFlightRef = useRef(false);
  const requestSequenceRef = useRef(0);
  const pollingGenerationRef = useRef(0);

  useEffect(() => {
    fetchTags();
  }, [sourceUnit]);

  useEffect(
    () => () => {
      stopPolling();
      abortActiveRequest(false);
    },
    []
  );

  useEffect(() => {
    if (!selectedTag) return;

    if (rangeMode === "latest") {
      stopPolling();
      const pollingGeneration = pollingGenerationRef.current;
      abortActiveRequest();
      runLiveFetch(selectedTag).finally(() =>
        restartPolling(selectedTag, pollingGeneration)
      );
    } else {
      stopPolling();
      abortActiveRequest();
      setLiveResult(null);
      setError("");
    }

    return () => {
      stopPolling();
      abortActiveRequest();
    };
  }, [
    selectedTag,
    sourceUnit,
    rangeMode,
    timeRangeHours,
    refreshSeconds,
    smoothWindow,
    customStartTime,
    customEndTime,
  ]);

  const fetchTags = async () => {
    try {
      const res = await api.get("/api/tags", { params: { unit: sourceUnit } });
      const tagsList = res.data.tags || [];
      setTags(tagsList);

      if (tagsList.length > 0 && !selectedTag) {
        setSelectedTag(tagsList[0].tag_id);
      }
    } catch (err) {
      console.error("Plant list load error:", err);
      setError("The plant list could not be loaded. Please try again.");
    }
  };

  const handleTagChange = (tagId) => {
    setSelectedTag(tagId);
  };

  const handleSourceUnitChange = (unit) => {
    setSourceUnit(unit);
    setSelectedTag("");
    setLiveResult(null);
    setError("");
  };

  const runLiveFetch = async (tagToUse = selectedTag) => {
    if (!tagToUse) return false;
    if (requestInFlightRef.current) return false;

    let customRangePayload = {};
    if (rangeMode === "custom") {
      if (!customStartTime || !customEndTime) {
        setError("Please choose both a start date/time and an end date/time.");
        return false;
      }

      if (!isValidPlantDateTimeRange(customStartTime, customEndTime)) {
        setError("End date/time must be later than start date/time.");
        return false;
      }

      customRangePayload = {
        start_time: toPlantRequestDateTime(customStartTime),
        stop_time: toPlantRequestDateTime(customEndTime),
      };
    }

    const requestId = ++requestSequenceRef.current;
    const controller = new AbortController();
    abortControllerRef.current = controller;
    requestInFlightRef.current = true;

      try {
        setLoading(true);
        setError("");

        const res = await api.post(
          "/api/live-monitor",
          {
            tag_id: tagToUse,
            source_unit: sourceUnit,
            smooth_window: Number(smoothWindow),
            last_n_hours: Number(timeRangeHours),
            ...customRangePayload,
          },
          {
            timeout: rangeMode === "custom" ? 120000 : 30000,
            signal: controller.signal,
          }
        );

        if (requestId !== requestSequenceRef.current) return false;
        setLiveResult(res.data);
        return true;
      } catch (err) {
        if (
          requestId !== requestSequenceRef.current ||
          err?.code === "ERR_CANCELED" ||
          err?.name === "CanceledError"
        ) return false;
        if (err.code === "ECONNABORTED") {
          setError(
            rangeMode === "custom"
              ? "The selected historical range took too long to load. Please try a shorter range."
              : "Live data took too long to load. Please try again."
          );
        } else {
          console.error("Live monitoring load error:", err);
          const status = Number(err?.response?.status || 0);
          setError(
            status > 0 && status < 500 && err?.response?.data?.detail
              ? String(err.response.data.detail)
              : "Live data could not be retrieved. Please try again."
          );
        }
        setLiveResult(null);
        return false;
      } finally {
        if (requestId === requestSequenceRef.current) {
          requestInFlightRef.current = false;
          abortControllerRef.current = null;
          setLoading(false);
        }
      }
  };

  const abortActiveRequest = (updateLoading = true) => {
    requestSequenceRef.current += 1;
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    requestInFlightRef.current = false;
    if (updateLoading) setLoading(false);
  };

  const clearPollTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const stopPolling = () => {
    clearPollTimer();
    pollingGenerationRef.current += 1;
  };

  const restartPolling = (
    tagToUse = selectedTag,
    generation = pollingGenerationRef.current
  ) => {
    clearPollTimer();
    if (
      generation !== pollingGenerationRef.current ||
      rangeMode !== "latest" ||
      document.hidden
    ) return;
    timerRef.current = setTimeout(async () => {
      await runLiveFetch(tagToUse);
      restartPolling(tagToUse, generation);
    }, Number(refreshSeconds) * 1000);
  };

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden) {
        stopPolling();
        abortActiveRequest();
        return;
      }

      if (selectedTag && rangeMode === "latest") {
        stopPolling();
        const pollingGeneration = pollingGenerationRef.current;
        runLiveFetch(selectedTag).finally(() =>
          restartPolling(selectedTag, pollingGeneration)
        );
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [selectedTag, rangeMode, refreshSeconds]);

  const formatDateTime = (value) => {
    return value
      ? formatPlantDateTime(value, {
          month: "2-digit",
          second: "2-digit",
          hourCycle: "h23",
        })
      : "-";
  };

  const handleApplyControls = async () => {
    const loaded = await runLiveFetch(selectedTag);
    if (loaded) setParameterOpen(false);
  };

  const selectedTagMeta = tags.find((item) => item.tag_id === selectedTag);
  const selectedRangeLabel = rangeMode === "custom"
    ? "Custom date/time"
    : `Latest ${timeRangeHours} hours`;

  return (
    <div className="app-shell">
      <TopNav />

      <main className="app-main live-monitor-page">
        <header className="dashboard-topbar">
          <div>
            <h1>Live Monitoring</h1>
          </div>

          <div className="dashboard-actions">
            <button type="button" className="btn-secondary" onClick={() => setParameterOpen(true)}>Monitoring Setup</button>
            <button type="button" className="btn-primary" disabled={loading || !selectedTag} onClick={() => runLiveFetch(selectedTag)}>
              {loading ? "Loading..." : rangeMode === "custom" ? "View Range" : "Refresh"}
            </button>
          </div>
        </header>

        <div className="filter-summary-card">
          <span>
            <strong>Plant:</strong>{" "}
            {selectedTagMeta
              ? (selectedTagMeta.display_name || selectedTagMeta.tag_name) !== selectedTagMeta.tag_id
                ? `${selectedTagMeta.display_name || selectedTagMeta.tag_name} (${selectedTagMeta.tag_id})`
                : selectedTagMeta.tag_id
              : selectedTag || "Not selected"}
          </span>
          <span><strong>Unit:</strong> {sourceUnit}</span>
          <span><strong>Sterilizers:</strong> {liveResult?.sterilizer_count || 0}</span>
          <span><strong>Active cycles:</strong> {liveResult?.active_cycle_count || 0}</span>
          <span><strong>Range:</strong> {selectedRangeLabel}</span>
          <span><strong>Refresh:</strong> {rangeMode === "custom" ? "Manual" : `Every ${refreshSeconds}s`}</span>
          {liveResult?.start_time && liveResult?.stop_time && (
            <span>
              <strong>Window:</strong> {formatDateTime(liveResult.start_time)} to {formatDateTime(liveResult.stop_time)}
            </span>
          )}
          {liveResult?.aggregate_every && (
            <span><strong>Sampling:</strong> {liveResult.aggregate_every}</span>
          )}
          <span><strong>Updated:</strong> {liveResult?.updated_at ? formatDateTime(liveResult.updated_at) : "-"}</span>
        </div>

        {error && <div className="error-box">{error}</div>}

        {loading && !liveResult ? (
          <div className="dash-card"><div className="empty-state">Loading live sterilizer data...</div></div>
        ) : liveResult?.sterilizers?.length ? (
          <div className="live-grid full-live-grid compact-live-grid">
            {liveResult.sterilizers.map((item) => (
              <SterilizerLiveCard
                key={item.field}
                sterilizer={item}
                unit={sourceUnit}
                compact
              />
            ))}
          </div>
        ) : (
          <div className="dash-card">
            <div className="empty-state live-monitor-empty-state">
              <strong>Select a plant to start monitoring.</strong>
              <span>Choose a plant and time range in Monitoring Setup.</span>
              <button type="button" className="btn-primary" onClick={() => setParameterOpen(true)}>
                Open Monitoring Setup
              </button>
            </div>
          </div>
        )}

        <LiveParameterDrawer
          open={parameterOpen}
          onClose={() => setParameterOpen(false)}
          tags={tags}
          selectedTag={selectedTag}
          onTagChange={handleTagChange}
          sourceUnit={sourceUnit}
        setSourceUnit={handleSourceUnitChange}
          rangeMode={rangeMode}
          setRangeMode={setRangeMode}
          timeRangeHours={timeRangeHours}
          setTimeRangeHours={setTimeRangeHours}
          customStartTime={customStartTime}
          setCustomStartTime={setCustomStartTime}
          customEndTime={customEndTime}
          setCustomEndTime={setCustomEndTime}
          refreshSeconds={refreshSeconds}
          setRefreshSeconds={setRefreshSeconds}
          smoothWindow={smoothWindow}
          setSmoothWindow={setSmoothWindow}
          onRefresh={handleApplyControls}
          loading={loading}
        />
      </main>
    </div>
  );
}
