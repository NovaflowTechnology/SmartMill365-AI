import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api";
import QueryForm from "../components/QueryForm";
import CycleTable from "../components/CycleTable";
import BenchmarkPreview from "../components/BenchmarkPreview";
import BenchmarkSelector from "../components/BenchmarkSelector";
import TopNav from "../components/TopNav";
import { getSterilizerDisplayName } from "../utils/sterilizerDisplay";

function BenchmarkParameterDrawer({
  open,
  onClose,
  formData,
  setFormData,
  onDetect,
  loading,
}) {
  return (
    <div className={`parameter-drawer-backdrop ${open ? "open" : ""}`}>
      <div className="parameter-drawer-panel">
        <div className="parameter-drawer-header">
          <div>
            <h2>Benchmark Parameters</h2>
            <p>
              Select historical pressure data to detect cycles and create a
              benchmark.
            </p>
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
            The generated benchmark will be used later for AI Comparison.
          </div>
        </div>
      </div>
    </div>
  );
}

export default function BenchmarkPage() {
  const navigate = useNavigate();
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

  const [detectResult, setDetectResult] = useState(null);
  const [selectedCycles, setSelectedCycles] = useState([]);
  const [benchmarks, setBenchmarks] = useState([]);
  const [selectedBenchmarkFile, setSelectedBenchmarkFile] = useState("");
  const [loadedBenchmark, setLoadedBenchmark] = useState(null);
  const [benchmarkName, setBenchmarkName] = useState("default_benchmark");
  const [normalizedPoints, setNormalizedPoints] = useState(300);

  const [loading, setLoading] = useState(false);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchBenchmarks();
  }, []);

  const fetchBenchmarks = async () => {
    try {
      const res = await api.get("/api/benchmarks");
      setBenchmarks(res.data.benchmarks || []);
    } catch (err) {
      console.error("Library load error:", err);
    }
  };

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

  const handleDetect = async () => {
    const validationError = validateQueryForm();

    if (validationError) {
      setError(validationError);
      setDetectResult(null);
      return;
    }

    try {
      setLoading(true);
      setError("");

      const res = await api.post("/api/detect-cycles", formData);

      setDetectResult(res.data);
      setSelectedCycles(res.data.cycles.map((_, idx) => idx));
      setParameterOpen(false);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
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
      !Number.isFinite(Number(normalizedPoints)) ||
      Number(normalizedPoints) < 10
    ) {
      setError("Normalized Points must be at least 10.");
      return;
    }

    try {
      setLoading(true);
      setError("");

      const payload = {
        ...formData,
        selected_cycle_indices: selectedCycles,
        normalized_points: normalizedPoints,
        benchmark_name: benchmarkName,
      };

      const res = await api.post("/api/build-benchmark", payload);
      setLoadedBenchmark(res.data.benchmark);

      if (res.data?.benchmark?.file_name) {
        setSelectedBenchmarkFile(res.data.benchmark.file_name);
        localStorage.setItem(
          "selectedBenchmarkFile",
          res.data.benchmark.file_name
        );
      } else if (selectedBenchmarkFile) {
        localStorage.setItem("selectedBenchmarkFile", selectedBenchmarkFile);
      }

      localStorage.setItem(
        "loadedBenchmark",
        JSON.stringify(res.data.benchmark)
      );

      await fetchBenchmarks();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleLoadBenchmark = async () => {
    if (!selectedBenchmarkFile) {
      setError("Please choose a benchmark first.");
      return;
    }

    try {
      setBenchmarkLoading(true);
      setError("");

      const res = await api.get(`/api/benchmarks/${selectedBenchmarkFile}`);

      setLoadedBenchmark(res.data.benchmark);
      localStorage.setItem("selectedBenchmarkFile", selectedBenchmarkFile);
      localStorage.setItem(
        "loadedBenchmark",
        JSON.stringify(res.data.benchmark)
      );
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setBenchmarkLoading(false);
    }
  };

  const goToComparisonPage = () => {
    if (!selectedBenchmarkFile) {
      setError("Please choose a benchmark before going to comparison page.");
      return;
    }

    navigate("/compare", {
      state: {
        selectedBenchmarkFile,
        loadedBenchmark,
      },
    });
  };

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
              <h1>Benchmark Management</h1>

              {selectedSterilizerLabel && (
                <span className="dashboard-sterilizer-pill">
                  {selectedSterilizerLabel}
                </span>
              )}
            </div>

            <p>
              Detect historical cycles, create benchmarks, and manage saved
              benchmark files.
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
              onClick={handleDetect}
              disabled={loading}
            >
              {loading ? "Detecting..." : "Detect Cycles"}
            </button>
          </div>
        </header>

        {error && <div className="error-box">{error}</div>}

        <section className="dash-card dashboard-card-large">
          <div className="dash-card-title">Detected Cycle Summary</div>

          {detectResult && detectResult.cycles ? (
            <CycleTable
              cycles={detectResult.cycles}
              selectedCycles={selectedCycles}
              setSelectedCycles={setSelectedCycles}
            />
          ) : (
            <div className="empty-state">
              Open Parameters and run “Detect Cycles” to preview detected cycle
              segments.
            </div>
          )}
        </section>

        {detectResult && (
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

              <div className="field-group benchmark-points-field">
                <label>Normalized Points</label>
                <input
                  type="number"
                  value={normalizedPoints}
                  onChange={(e) =>
                    setNormalizedPoints(Number(e.target.value))
                  }
                />
              </div>

              <button
                type="button"
                className="btn-primary"
                onClick={handleBuildBenchmark}
                disabled={loading}
              >
                {loading ? "Processing..." : "Create Benchmark"}
              </button>
            </div>
          </section>
        )}

        <section className="dash-card dashboard-card-large">
          <div className="dash-card-title">Saved Benchmarks Library</div>

          <BenchmarkSelector
            benchmarks={benchmarks}
            selectedBenchmarkFile={selectedBenchmarkFile}
            setSelectedBenchmarkFile={setSelectedBenchmarkFile}
            onLoadBenchmark={handleLoadBenchmark}
            loading={benchmarkLoading}
          />

          <div className="button-row left-align" style={{ marginTop: "16px" }}>
            <button
              type="button"
              className="btn-primary"
              onClick={goToComparisonPage}
            >
              Go to AI Comparison
            </button>
          </div>
        </section>

        <section className="dash-card dashboard-card-large">
          <div className="dash-card-title">Benchmark Preview</div>

          {loadedBenchmark ? (
            <BenchmarkPreview benchmark={loadedBenchmark} />
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
          setFormData={setFormData}
          onDetect={handleDetect}
          loading={loading}
        />
      </main>
    </div>
  );
}