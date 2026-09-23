import React, { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import api from "../api";
import QueryForm from "../components/QueryForm";
import CycleTable from "../components/CycleTable";
import BenchmarkPreview from "../components/BenchmarkPreview";
import BenchmarkSelector from "../components/BenchmarkSelector";
import TopNav from "../components/TopNav";
import NotificationToast from "../components/NotificationToast";
import ProfessionalModal from "../components/ProfessionalModal";
import { getSterilizerDisplayName } from "../utils/sterilizerDisplay";
import {
  isValidPlantDateTimeRange,
  toPlantRequestDateTime,
} from "../utils/plantTime";
import {
  runLatestPageTask,
  setPageSessionValue,
  usePageSessionState,
} from "../state/pageSessionStore";

const NORMALIZED_POINTS = 300;

function benchmarkDataId(benchmark) {
  return benchmark?.tag_id || benchmark?.sterilizer_id || benchmark?.id || "";
}

function benchmarkDisplayName(benchmark) {
  return benchmark?.benchmark_name || benchmark?.file_name || "Benchmark";
}

function detectionParameters(formData) {
  return {
    bucket: formData.bucket || "",
    field: formData.field || "",
    tag_id: formData.tag_id || "",
    source_unit: formData.source_unit || "bar",
    start_time: toPlantRequestDateTime(formData.start_time) || formData.start_time || "",
    stop_time: toPlantRequestDateTime(formData.stop_time) || formData.stop_time || "",
    smooth_window: Number(formData.smooth_window || 0),
  };
}

function detectionKey(formData) {
  return JSON.stringify(detectionParameters(formData));
}

function BenchmarkParameterDrawer({
  open,
  onClose,
  formData,
  setFormData,
  onDetect,
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
      <div className="parameter-drawer-panel" role="dialog" aria-modal="true" aria-label="Benchmark Setup">
        <div className="parameter-drawer-header">
          <div>
            <h2>Benchmark Setup</h2>
          </div>

          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="parameter-drawer-body">
          <QueryForm
            formData={formData}
            setFormData={setFormData}
            onDetect={onDetect}
            loading={loading}
            submitLabel="Detect Cycles"
          />

          <div className="info-banner">
            Select a stable historical time range with representative cycles.
            After creation, assign the benchmark as active before it is used by analysis and reporting.
          </div>
        </div>
      </div>
    </div>
  );
}

function ActiveBenchmarkConfirmModal({ confirmation, loading, onCancel, onConfirm }) {
  return (
    <ProfessionalModal
      open={Boolean(confirmation)}
      title="Replace Active Benchmark?"
      subtitle="This sterilizer already has an active benchmark. Review the change before continuing."
      tone="warning"
      onClose={() => !loading && onCancel()}
      closeOnBackdrop={!loading}
      labelledBy="replace-active-benchmark-title"
      actions={
        <>
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={onConfirm} disabled={loading}>
            {loading ? "Replacing..." : "Replace Benchmark"}
          </button>
        </>
      }
    >
      {confirmation ? (
        <>
          <div className="benchmark-replacement-summary benchmark-replacement-summary--professional">
            <div>
              <span>Current Active Benchmark</span>
              <strong>{confirmation.currentName}</strong>
            </div>
            <div className="benchmark-replacement-arrow" aria-hidden="true">→</div>
            <div>
              <span>New Benchmark</span>
              <strong>{confirmation.newName}</strong>
            </div>
          </div>
          <div className="security-note-box benchmark-replacement-impact">
            New AI Comparison runs, Daily Reports, and new AI Chatbot analyses will use the new active benchmark for <strong>{confirmation.dataId}</strong> / <strong>{confirmation.sterilizerLabel}</strong>.
          </div>
        </>
      ) : null}
    </ProfessionalModal>
  );
}

export default function BenchmarkPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const prefillAppliedRef = useRef(false);
  const [parameterOpen, setParameterOpen] = useState(false);

  const [formData, setFormData] = usePageSessionState(
    "benchmark",
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

  const [detectResult, setDetectResult] = usePageSessionState(
    "benchmark",
    "detectResult",
    null,
    { persist: false }
  );
  const [selectedCycles, setSelectedCycles] = usePageSessionState(
    "benchmark",
    "selectedCycles",
    [],
    { persist: false }
  );
  const [lastDetectedParameters, setLastDetectedParameters] = usePageSessionState(
    "benchmark",
    "lastDetectedParameters",
    null,
    { persist: false }
  );
  const [benchmarks, setBenchmarks] = usePageSessionState(
    "benchmark",
    "benchmarks",
    [],
    { persist: false }
  );
  const [selectedBenchmarkId, setSelectedBenchmarkId] = usePageSessionState(
    "benchmark",
    "selectedBenchmarkId",
    () => localStorage.getItem("selectedBenchmarkId") || ""
  );
  const formDataRef = useRef(formData);
  formDataRef.current = formData;
  const [loadedBenchmark, setLoadedBenchmark] = usePageSessionState(
    "benchmark",
    "loadedBenchmark",
    () => {
      const saved = localStorage.getItem("loadedBenchmark");
      try {
        return saved ? JSON.parse(saved) : null;
      } catch {
        return null;
      }
    },
    { persist: false }
  );
  const [benchmarkName, setBenchmarkName] = usePageSessionState(
    "benchmark",
    "benchmarkName",
    "default_benchmark"
  );
  const [activeAssignments, setActiveAssignments] = useState({});
  const [activationLoading, setActivationLoading] = useState(false);
  const [activationConfirm, setActivationConfirm] = useState(null);
  const [creationNotice, setCreationNotice] = useState(null);
  const [activationMessage, setActivationMessage] = useState("");

  const [loading, setLoading] = usePageSessionState(
    "benchmark",
    "loading",
    false,
    { persist: false }
  );
  const [benchmarkLoading, setBenchmarkLoading] = usePageSessionState(
    "benchmark",
    "benchmarkLoading",
    false,
    { persist: false }
  );
  const [error, setError] = usePageSessionState(
    "benchmark",
    "error",
    "",
    { persist: false }
  );
  const [libraryError, setLibraryError] = usePageSessionState(
    "benchmark",
    "libraryError",
    "",
    { persist: false }
  );

  const refreshActiveAssignments = async () => {
    try {
      const response = await api.get("/api/daily-report/settings");
      const assignments = response.data?.settings?.active_benchmarks || {};
      setActiveAssignments(assignments);
      return response.data?.settings || null;
    } catch (err) {
      console.error("Active benchmark settings load error:", err);
      return null;
    }
  };

  useEffect(() => {
    fetchBenchmarks();
    refreshActiveAssignments();
  }, []);

  const fetchBenchmarks = async () => runLatestPageTask(
    "benchmark:library",
    async ({ isLatest }) => {
      try {
        setLibraryError("");
        const res = await api.get("/api/benchmarks");
        if (isLatest()) {
          const items = res.data.benchmarks || [];
          setBenchmarks(items);
          setPageSessionValue("settings", "benchmarks", items, { persist: false });
          const savedId = localStorage.getItem("selectedBenchmarkId") || selectedBenchmarkId;
          const legacyFile = localStorage.getItem("selectedBenchmarkFile") || "";
          const selected = items.find(
            (item) =>
              item.benchmark_id === savedId ||
              item.database_id === savedId ||
              item.file_name === legacyFile
          );
          if (selected) {
            const identifier = selected.benchmark_id || selected.database_id || selected.file_name;
            setSelectedBenchmarkId(identifier);
            localStorage.setItem("selectedBenchmarkId", identifier);
            localStorage.removeItem("selectedBenchmarkFile");
          }
        }
      } catch (err) {
        if (isLatest()) {
          console.error("Library load error:", err);
          setLibraryError("Saved benchmarks could not be loaded. Please try again.");
        }
      }
    }
  );

  const handleAdjustedBenchmarkSaved = async (response) => {
    const fileName = response?.file_name || response?.benchmark?.file_name;
    const benchmarkId =
      response?.benchmark?.database_id || response?.benchmark?.benchmark_id;
    if (response?.benchmark) {
      setLoadedBenchmark(response.benchmark);
      setCreationNotice({
        benchmark: response.benchmark,
        title: `New benchmark “${benchmarkDisplayName(response.benchmark)}” created.`,
        message: "The active benchmark has not changed.",
        actionLabel: "Set New Benchmark as Active",
      });
      localStorage.setItem("loadedBenchmark", JSON.stringify(response.benchmark));
    }
    if (benchmarkId || fileName) {
      const identifier = benchmarkId || fileName;
      setSelectedBenchmarkId(identifier);
      localStorage.setItem("selectedBenchmarkId", identifier);
    }
    await fetchBenchmarks();
  };

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

  const handleFormDataChange = (updater) => {
    const nextFormData =
      typeof updater === "function" ? updater(formDataRef.current) : updater;
    formDataRef.current = nextFormData;
    setFormData(nextFormData);

    if (
      lastDetectedParameters &&
      detectionKey(nextFormData) !== detectionKey(lastDetectedParameters)
    ) {
      setDetectResult(null);
      setSelectedCycles([]);
      setLastDetectedParameters(null);
      setError("");
    }
  };

  useEffect(() => {
    const prefill = location.state?.benchmarkPrefill;
    if (!prefill || prefillAppliedRef.current) return;

    prefillAppliedRef.current = true;
    const nextFormData = {
      ...formDataRef.current,
      tag_id: prefill.tag_id || "",
      field: prefill.field || "",
      plant_display_name: prefill.plant_display_name || prefill.tag_id || "",
      source_unit: prefill.source_unit || formDataRef.current.source_unit || "bar",
      start_time: "",
      stop_time: "",
    };

    formDataRef.current = nextFormData;
    setFormData(nextFormData);
    setDetectResult(null);
    setSelectedCycles([]);
    setLastDetectedParameters(null);
    setError("");
    setParameterOpen(true);
    navigate("/benchmark", { replace: true, state: null });
  }, [location.state, navigate, setDetectResult, setFormData, setLastDetectedParameters, setSelectedCycles]);

  const handleDetect = async () => {
    const validationError = validateQueryForm();

    if (validationError) {
      setError(validationError);
      setDetectResult(null);
      setSelectedCycles([]);
      setLastDetectedParameters(null);
      return;
    }

    const requestedParameters = detectionParameters(formData);
    const requestedKey = detectionKey(requestedParameters);

    return runLatestPageTask("benchmark:detect", async ({ isLatest }) => {
    try {
      setLoading(true);
      setError("");
      setDetectResult(null);
      setSelectedCycles([]);
      setLastDetectedParameters(null);

      const res = await api.post("/api/detect-cycles", requestedParameters);

      if (!isLatest() || detectionKey(formDataRef.current) !== requestedKey) return;
      setDetectResult(res.data);
      setSelectedCycles(res.data.cycles.map((_, idx) => idx));
      setLastDetectedParameters(requestedParameters);
      setParameterOpen(false);
    } catch (err) {
      if (isLatest() && detectionKey(formDataRef.current) === requestedKey) {
        console.error("Cycle detection error:", err);
        if (err?.response?.status && err.response.status < 500) {
          setError(err.response.data?.detail || "Cycles could not be detected. Check the Benchmark Setup and try again.");
        } else {
          setError("Cycles could not be detected. Please try again.");
        }
      }
    } finally {
      if (isLatest()) setLoading(false);
    }
    });
  };

  const handleBuildBenchmark = async () => {
    const validationError = validateQueryForm();

    if (validationError) {
      setError(validationError);
      return;
    }

    if (!benchmarkName?.trim()) {
      setError("Please enter Benchmark Name.");
      return;
    }

    if (!selectedCycles.length) {
      setError("Please select at least one cycle to build benchmark.");
      return;
    }

    if (
      !detectResult ||
      !lastDetectedParameters ||
      detectionKey(formData) !== detectionKey(lastDetectedParameters)
    ) {
      setError("Detection parameters changed. Click Detect Cycles again before creating a benchmark.");
      return;
    }

    return runLatestPageTask("benchmark:build", async ({ isLatest }) => {
    try {
      setLoading(true);
      setError("");

      const payload = {
        ...lastDetectedParameters,
        selected_cycle_indices: selectedCycles,
        selected_cycle_boundaries: selectedCycles.map((index) => ({
          start: detectResult.cycles[index].start,
          end: detectResult.cycles[index].end,
        })),
        detection_fingerprint: detectResult.detection_fingerprint,
        normalized_points: NORMALIZED_POINTS,
        benchmark_name: benchmarkName,
      };

      const res = await api.post("/api/build-benchmark", payload);
      if (!isLatest()) return;
      setLoadedBenchmark(res.data.benchmark);
      setCreationNotice({
        benchmark: res.data.benchmark,
        title: `Benchmark “${benchmarkDisplayName(res.data.benchmark)}” created successfully.`,
        message: "This benchmark is not active yet. Set it as active if you want it to be used by AI Comparison, Daily Report, and new AI Chatbot analyses.",
        actionLabel: "Set as Active",
      });

      const createdIdentifier =
        res.data?.benchmark?.database_id || res.data?.benchmark?.file_name;
      if (createdIdentifier) {
        setSelectedBenchmarkId(createdIdentifier);
        localStorage.setItem(
          "selectedBenchmarkId",
          createdIdentifier
        );
      }

      localStorage.setItem(
        "loadedBenchmark",
        JSON.stringify(res.data.benchmark)
      );

      await fetchBenchmarks();
    } catch (err) {
      if (isLatest()) {
        console.error("Benchmark creation error:", err);
        setError(
          err?.response?.status && err.response.status < 500
            ? err.response.data?.detail || "The benchmark could not be created. Check the selected cycles and try again."
            : "The benchmark could not be created. Please try again."
        );
        if (err?.response?.status === 409) {
          setDetectResult(null);
          setSelectedCycles([]);
          setLastDetectedParameters(null);
        }
      }
    } finally {
      if (isLatest()) setLoading(false);
    }
    });
  };

  const handleLoadBenchmark = async () => {
    if (!selectedBenchmarkId) {
      setError("Please choose a benchmark first.");
      return;
    }

    return runLatestPageTask("benchmark:load", async ({ isLatest }) => {
    try {
      setBenchmarkLoading(true);
      setError("");

      const res = await api.get(
        `/api/benchmarks/${encodeURIComponent(selectedBenchmarkId)}`
      );

      if (!isLatest()) return;
      setLoadedBenchmark(res.data.benchmark);
      setCreationNotice(null);
      localStorage.setItem("selectedBenchmarkId", selectedBenchmarkId);
      localStorage.setItem(
        "loadedBenchmark",
        JSON.stringify(res.data.benchmark)
      );
    } catch (err) {
      if (isLatest()) {
        console.error("Benchmark load error:", err);
        setError("The selected benchmark could not be loaded. Please try again.");
      }
    } finally {
      if (isLatest()) setBenchmarkLoading(false);
    }
    });
  };

  const saveActiveBenchmark = async (settingsSnapshot, benchmark) => {
    const dataId = benchmarkDataId(benchmark);
    const field = benchmark?.field || "";
    const fileName = benchmark?.file_name || "";

    if (!dataId || !field || !fileName) {
      setError("This benchmark is missing the plant, sterilizer, or file information required for activation.");
      return false;
    }

    const nextSettings = JSON.parse(JSON.stringify(settingsSnapshot || {}));
    nextSettings.active_benchmarks = nextSettings.active_benchmarks || {};
    nextSettings.active_benchmarks[dataId] = {
      ...(nextSettings.active_benchmarks[dataId] || {}),
      [field]: fileName,
    };

    try {
      setActivationLoading(true);
      setError("");
      const response = await api.put("/api/daily-report/settings", nextSettings);
      const savedSettings = response.data?.settings || nextSettings;
      const savedAssignments = savedSettings.active_benchmarks || nextSettings.active_benchmarks;
      setActiveAssignments(savedAssignments);
      setPageSessionValue("settings", "draft", (current) => {
        if (!current) return current;
        return {
          ...current,
          version: savedSettings.version ?? current.version,
          active_benchmarks: JSON.parse(JSON.stringify(savedAssignments)),
        };
      });
      setActivationMessage(`“${benchmarkDisplayName(benchmark)}” is now the active benchmark.`);
      setCreationNotice(null);
      setActivationConfirm(null);
      return true;
    } catch (err) {
      console.error("Set active benchmark error:", err);
      if (err?.response?.status === 409) {
        setError("Settings changed while you were updating the active benchmark. Please try again.");
      } else {
        setError("The active benchmark could not be updated. Please try again.");
      }
      return false;
    } finally {
      setActivationLoading(false);
    }
  };

  const handleRequestSetActive = async (benchmark) => {
    const dataId = benchmarkDataId(benchmark);
    const field = benchmark?.field || "";
    const fileName = benchmark?.file_name || "";

    if (!dataId || !field || !fileName) {
      setError("This benchmark is missing the plant, sterilizer, or file information required for activation.");
      return;
    }

    try {
      setActivationLoading(true);
      setError("");
      const response = await api.get("/api/daily-report/settings");
      const settingsSnapshot = response.data?.settings || null;
      if (!settingsSnapshot) {
        setError("The saved Settings configuration could not be loaded. Please try again.");
        return;
      }

      const currentFile = settingsSnapshot.active_benchmarks?.[dataId]?.[field] || "";
      setActiveAssignments(settingsSnapshot.active_benchmarks || {});

      if (currentFile === fileName) {
        setCreationNotice(null);
        setActivationMessage(`“${benchmarkDisplayName(benchmark)}” is already the active benchmark.`);
        return;
      }

      if (!currentFile) {
        await saveActiveBenchmark(settingsSnapshot, benchmark);
        return;
      }

      const currentBenchmark = benchmarks.find((item) => item.file_name === currentFile);
      setActivationConfirm({
        benchmark,
        settingsSnapshot,
        currentName: currentBenchmark ? benchmarkDisplayName(currentBenchmark) : currentFile,
        newName: benchmarkDisplayName(benchmark),
        dataId,
        sterilizerLabel: getSterilizerDisplayName(dataId, field),
      });
    } catch (err) {
      console.error("Active benchmark lookup error:", err);
      setError("The current active benchmark could not be checked. Please try again.");
    } finally {
      setActivationLoading(false);
    }
  };

  const loadedBenchmarkFile = loadedBenchmark?.file_name || "";
  const loadedBenchmarkDataId = benchmarkDataId(loadedBenchmark);
  const loadedBenchmarkField = loadedBenchmark?.field || "";
  const loadedBenchmarkIsActive = Boolean(
    loadedBenchmarkFile &&
      loadedBenchmarkDataId &&
      loadedBenchmarkField &&
      activeAssignments?.[loadedBenchmarkDataId]?.[loadedBenchmarkField] === loadedBenchmarkFile
  );

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

      <main className="app-main">
        <header className="dashboard-topbar compact-dashboard-topbar">
          <div className="dashboard-title-group">
            <div className="dashboard-title-row">
              <h1>Benchmark Management</h1>

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
              Benchmark Setup
            </button>

            <button
              type="button"
              className="btn-primary"
              onClick={handleDetect}
              disabled={loading}
            >
              {loading ? "Detecting..." : "Detect Cycles"}
            </button>
          </div>
        </header>

        {error && <div className="error-box">{error}</div>}

        <NotificationToast
          message={activationMessage}
          tone="success"
          autoDismissMs={3000}
          onClose={() => setActivationMessage("")}
        />

        {creationNotice && (
          <div className="benchmark-created-banner" role="status">
            <div>
              <strong>{creationNotice.title}</strong>
              <span>{creationNotice.message}</span>
            </div>
            <div className="benchmark-created-actions">
              <button
                type="button"
                className="btn-primary"
                disabled={activationLoading}
                onClick={() => handleRequestSetActive(creationNotice.benchmark)}
              >
                {activationLoading ? "Checking..." : creationNotice.actionLabel}
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setCreationNotice(null)}
                disabled={activationLoading}
              >
                Close
              </button>
            </div>
          </div>
        )}

        <section className="dash-card dashboard-card-large">
          <div className="dash-card-title">Detected Cycle Summary</div>

          {detectResult && detectResult.cycles ? (
            <CycleTable
              cycles={detectResult.cycles}
              selectedCycles={selectedCycles}
              setSelectedCycles={setSelectedCycles}
              times={detectResult.times}
              values={detectResult.smooth}
              sourceUnit={detectResult.source_unit}
            />
          ) : (
            <div className="empty-state">
              Open Benchmark Setup and run “Detect Cycles” to preview detected cycle
              segments.
            </div>
          )}
        </section>

        <section className="dash-card dashboard-card-large">
            <div className="dash-card-title">Create New Benchmark</div>

            <div className="benchmark-create-row">
              <div className="field-group benchmark-name-field">
                <label>Benchmark Name</label>
                <input
                  value={benchmarkName}
                  onChange={(e) => setBenchmarkName(e.target.value)}
                />
              </div>

              <button
                type="button"
                className="btn-primary benchmark-create-button"
                onClick={handleBuildBenchmark}
                disabled={
                  loading ||
                  !detectResult ||
                  !selectedCycles.length ||
                  !lastDetectedParameters ||
                  detectionKey(formData) !== detectionKey(lastDetectedParameters)
                }
              >
                {loading ? "Processing..." : "Create Benchmark"}
              </button>
            </div>
        </section>

        <section className="dash-card dashboard-card-large">
          <div className="dash-card-title">Saved Benchmarks Library</div>

          {libraryError && (
            <div className="inline-retry-error" role="alert">
              <span>{libraryError}</span>
              <button
                type="button"
                className="btn-secondary"
                onClick={fetchBenchmarks}
              >
                Retry
              </button>
            </div>
          )}

          <BenchmarkSelector
            benchmarks={benchmarks}
            selectedBenchmarkId={selectedBenchmarkId}
            setSelectedBenchmarkId={setSelectedBenchmarkId}
            onLoadBenchmark={handleLoadBenchmark}
            loading={benchmarkLoading}
          />

        </section>

        <section className="dash-card dashboard-card-large">
          <div className="dash-card-title">Benchmark Preview</div>

          {loadedBenchmark ? (
            <BenchmarkPreview
              key={
                loadedBenchmark.benchmark_id ||
                loadedBenchmark.database_id ||
                loadedBenchmark.file_name
              }
              benchmark={loadedBenchmark}
              onSaved={handleAdjustedBenchmarkSaved}
              onSetActive={handleRequestSetActive}
              isActive={loadedBenchmarkIsActive}
              activationLoading={activationLoading}
            />
          ) : (
            <div className="empty-state">
              Load or create a benchmark to preview it here.
            </div>
          )}
        </section>

        <BenchmarkParameterDrawer
          open={parameterOpen}
          onClose={() => setParameterOpen(false)}
          formData={formData}
          setFormData={handleFormDataChange}
          onDetect={handleDetect}
          loading={loading}
        />

        <ActiveBenchmarkConfirmModal
          confirmation={activationConfirm}
          loading={activationLoading}
          onCancel={() => setActivationConfirm(null)}
          onConfirm={() =>
            activationConfirm &&
            saveActiveBenchmark(activationConfirm.settingsSnapshot, activationConfirm.benchmark)
          }
        />
      </main>
    </div>
  );
}
