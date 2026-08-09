import { useEffect, useRef, useState } from "react";
import api from "../api";
import TopNav from "../components/TopNav";
import SterilizerLiveCard from "../components/SterilizerLiveCard";

function LiveParameterDrawer({
  open,
  onClose,
  tags,
  selectedTag,
  onTagChange,
  sourceUnit,
  setSourceUnit,
  timeRangeHours,
  setTimeRangeHours,
  refreshSeconds,
  setRefreshSeconds,
  smoothWindow,
  setSmoothWindow,
  onRefresh,
  loading,
}) {
  return (
    <div className={`parameter-drawer-backdrop ${open ? "open" : ""}`}>
      <div className="parameter-drawer-panel">
        <div className="parameter-drawer-header">
          <div>
            <h2>Live Monitoring Controls</h2>
            <p>Choose the tag, unit, display window, and refresh interval.</p>
          </div>
          <button type="button" className="btn-secondary" onClick={onClose}>Close</button>
        </div>

        <div className="parameter-drawer-body">
          <div className="query-form-container">
            <div className="field-group">
              <label>Select Tag</label>
              <select value={selectedTag} onChange={(e) => onTagChange(e.target.value)}>
                <option value="">-- Choose Tag --</option>
                {tags.map((item) => (
                  <option key={item.tag_id} value={item.tag_id}>{item.tag_name} ({item.sterilizer_count} sterilizers)</option>
                ))}
              </select>
            </div>

            <div className="field-group">
              <label>Unit</label>
              <select value={sourceUnit} onChange={(e) => setSourceUnit(e.target.value)}>
                <option value="bar">bar</option>
                <option value="psi">psi</option>
              </select>
            </div>

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

            <div className="field-group">
              <label>Smooth Window</label>
              <input type="number" min="1" value={smoothWindow} onChange={(e) => setSmoothWindow(Number(e.target.value))} />
            </div>

            <div className="button-row left-align">
              <button type="button" onClick={onRefresh} disabled={loading || !selectedTag}>{loading ? "Refreshing..." : "Refresh Now"}</button>
            </div>

            <div className="live-monitor-note">
              The chart shows the latest {timeRangeHours} hours and refreshes automatically every {refreshSeconds} seconds.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LiveMonitoringPage() {
  const [parameterOpen, setParameterOpen] = useState(false);
  const [tags, setTags] = useState([]);
  const [selectedTag, setSelectedTag] = useState("");
  const [sourceUnit, setSourceUnit] = useState("bar");
  const [timeRangeHours, setTimeRangeHours] = useState(3);
  const [refreshSeconds, setRefreshSeconds] = useState(15);
  const [smoothWindow, setSmoothWindow] = useState(9);
  const [loading, setLoading] = useState(false);
  const [liveResult, setLiveResult] = useState(null);
  const [error, setError] = useState("");

  const timerRef = useRef(null);
  const requestInProgressRef = useRef(false);

  useEffect(() => {
    fetchTags();
    return () => stopPolling();
  }, []);

  useEffect(() => {
    if (!selectedTag) return;
    runLiveFetch(selectedTag);
    restartPolling(selectedTag);
    return () => stopPolling();
  }, [selectedTag, sourceUnit, timeRangeHours, refreshSeconds, smoothWindow]);

  const fetchTags = async () => {
    try {
      const res = await api.get("/api/tags");
      const tagsList = res.data.tags || [];
      setTags(tagsList);

      if (tagsList.length > 0 && !selectedTag) {
        setSelectedTag(tagsList[0].tag_id);
        if (tagsList[0].unit) setSourceUnit(tagsList[0].unit);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    }
  };

  const handleTagChange = (tagId) => {
    setSelectedTag(tagId);
    const selected = tags.find((item) => item.tag_id === tagId);
    if (selected?.unit) setSourceUnit(selected.unit);
  };

  const runLiveFetch = async (tagToUse = selectedTag) => {
    if (!tagToUse) return;
    if (requestInProgressRef.current) return;

    try {
      requestInProgressRef.current = true;
      setLoading(true);
      setError("");

      const res = await api.post(
        "/api/live-monitor",
        {
          bucket: "Mill",
          measurement: "PSTR",
          tag_id: tagToUse,
          source_unit: sourceUnit,
          smooth_window: Number(smoothWindow),
          last_n_hours: Number(timeRangeHours),
        },
        { timeout: 20000 }
      );

      setLiveResult(res.data);
    } catch (err) {
      if (err.code === "ECONNABORTED") {
        setError("Connection error: data could not be retrieved within 20 seconds. Please check the database connection or try a shorter time range.");
      } else {
        setError(err?.response?.data?.detail || err.message || "Connection error.");
      }
      setLiveResult(null);
    } finally {
      requestInProgressRef.current = false;
      setLoading(false);
    }
  };

  const stopPolling = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const restartPolling = (tagToUse = selectedTag) => {
    stopPolling();
    timerRef.current = setInterval(() => runLiveFetch(tagToUse), Number(refreshSeconds) * 1000);
  };

  const formatDateTime = (value) => {
    if (!value) return "-";
    return String(value).replace("T", " ").slice(0, 19);
  };

  const selectedTagMeta = tags.find((item) => item.tag_id === selectedTag);

  return (
    <div className="app-shell">
      <TopNav />

      <main className="app-main">
        <header className="dashboard-topbar">
          <div>
            <h1>Live Monitoring</h1>
            <p>Monitor current pressure curves for all sterilizers under the selected tag.</p>
          </div>

          <div className="dashboard-actions">
            <button type="button" className="btn-secondary" onClick={() => setParameterOpen(true)}>Controls</button>
            <button type="button" className="btn-primary" disabled={loading || !selectedTag} onClick={() => runLiveFetch(selectedTag)}>
              {loading ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </header>

        <div className="filter-summary-card">
          <span><strong>Tag:</strong> {selectedTagMeta?.tag_name || selectedTag || "Not selected"}</span>
          <span><strong>Unit:</strong> {sourceUnit}</span>
          <span><strong>Range:</strong> Latest {timeRangeHours} hours</span>
          <span><strong>Refresh:</strong> Every {refreshSeconds}s</span>
          <span><strong>Updated:</strong> {liveResult?.updated_at ? formatDateTime(liveResult.updated_at) : "-"}</span>
        </div>

        {error && <div className="error-box">{error}</div>}

        <div className="summary-grid dashboard-summary-grid">
          <div className="summary-card">
            <div className="summary-label">Selected Tag</div>
            <div className="summary-value live-summary-text">{liveResult?.tag_id || selectedTag || "-"}</div>
            <div className="summary-note">Current monitoring source</div>
          </div>

          <div className="summary-card">
            <div className="summary-label">Sterilizers</div>
            <div className="summary-value">{liveResult?.sterilizer_count || 0}</div>
            <div className="summary-note">Visible under selected tag</div>
          </div>

          <div className="summary-card">
            <div className="summary-label">Active Cycles</div>
            <div className="summary-value">{liveResult?.active_cycle_count || 0}</div>
            <div className="summary-note">Currently active recent cycles</div>
          </div>

          <div className="summary-card">
            <div className="summary-label">Display Range</div>
            <div className="summary-value live-summary-text">Latest {liveResult?.window_hours_used || timeRangeHours} hours</div>
            <div className="summary-note">{formatDateTime(liveResult?.start_time)} to {formatDateTime(liveResult?.stop_time)}</div>
            {liveResult?.aggregate_every && <div className="summary-note">Downsampled every {liveResult.aggregate_every}</div>}
            {liveResult?.performance?.total_seconds != null && <div className="summary-note">Loaded in {liveResult.performance.total_seconds}s</div>}
          </div>
        </div>

        {loading && !liveResult ? (
          <div className="dash-card"><div className="empty-state">Loading live sterilizer data...</div></div>
        ) : liveResult?.sterilizers?.length ? (
          <div className="live-grid full-live-grid">
            {liveResult.sterilizers.map((item) => (
              <SterilizerLiveCard key={item.field} sterilizer={item} unit={sourceUnit} />
            ))}
          </div>
        ) : (
          <div className="dash-card"><div className="empty-state">Open Controls and select a tag to view live sterilizer curves.</div></div>
        )}

        <LiveParameterDrawer
          open={parameterOpen}
          onClose={() => setParameterOpen(false)}
          tags={tags}
          selectedTag={selectedTag}
          onTagChange={handleTagChange}
          sourceUnit={sourceUnit}
          setSourceUnit={setSourceUnit}
          timeRangeHours={timeRangeHours}
          setTimeRangeHours={setTimeRangeHours}
          refreshSeconds={refreshSeconds}
          setRefreshSeconds={setRefreshSeconds}
          smoothWindow={smoothWindow}
          setSmoothWindow={setSmoothWindow}
          onRefresh={() => runLiveFetch(selectedTag)}
          loading={loading}
        />
      </main>
    </div>
  );
}
