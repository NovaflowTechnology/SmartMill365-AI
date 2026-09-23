import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Label,
} from "recharts";
import EditableBenchmarkCurve from "./EditableBenchmarkCurve";
import { getBenchmarkSterilizerName } from "../utils/sterilizerDisplay";

export default function BenchmarkPreview({
  benchmark,
  onSaved,
  onSetActive,
  isActive = false,
  activationLoading = false,
}) {
  const [currentBenchmark, setCurrentBenchmark] = useState(benchmark);
  const [showEditor, setShowEditor] = useState(false);

  useEffect(() => {
    setCurrentBenchmark(benchmark);
    setShowEditor(false);
  }, [benchmark]);

  if (!currentBenchmark) {
    return (
      <div className="empty-state">
        Load or create a benchmark to preview it here.
      </div>
    );
  }

  const unit = currentBenchmark.benchmark_unit || currentBenchmark.unit || "bar";
  const curve = currentBenchmark.benchmark_curve || [];
  const sterilizerName = getBenchmarkSterilizerName(currentBenchmark);

  const chartData = curve.map((value, index) => ({
    point: index + 1,
    benchmark: value,
  }));

  const handleSaveSuccess = (response) => {
    if (response?.benchmark) {
      setCurrentBenchmark(response.benchmark);
    }
    setShowEditor(false);
    if (onSaved) {
      onSaved(response);
    }
  };

  return (
    <div className="benchmark-preview-wrapper">
      <div className="benchmark-preview-mode-banner">
        <div>
          <span className="benchmark-preview-mode-badge">PREVIEW / EDIT MODE</span>
          <strong>Viewing or adjusting this benchmark does not change the active benchmark.</strong>
          <p>Adjusted curves are saved as a new benchmark.</p>
        </div>
        <div className="benchmark-preview-mode-actions">
          {isActive ? (
            <span className="active-benchmark-status">Active Benchmark ✓</span>
          ) : (
            <button
              type="button"
              className="btn-primary"
              onClick={() => onSetActive?.(currentBenchmark)}
              disabled={activationLoading}
            >
              {activationLoading ? "Updating..." : "Set as Active"}
            </button>
          )}
        </div>
      </div>

      <div className="benchmark-preview-card">
        <div className="benchmark-preview-info">
          <div className="info-box">
            <span className="info-label">Name</span>
            <span className="info-value">
              {currentBenchmark.benchmark_name || "-"}
            </span>
          </div>

          <div className="info-box">
            <span className="info-label">Data ID</span>
            <span className="info-value">
              {currentBenchmark.tag_id || currentBenchmark.sterilizer_id || currentBenchmark.id || "-"}
            </span>
          </div>

          <div className="info-box">
            <span className="info-label">Sterilizer</span>
            <span className="info-value">{sterilizerName}</span>
          </div>

          <div className="info-box">
            <span className="info-label">Unit</span>
            <span className="info-value">{unit}</span>
          </div>

          <div className="info-box">
            <span className="info-label">Cycle Count</span>
            <span className="info-value">
              {currentBenchmark.source_cycle_count || "-"}
            </span>
          </div>

          <div className="info-box">
            <span className="info-label">Created At</span>
            <span className="info-value">
              {currentBenchmark.created_at || "-"}
            </span>
          </div>
        </div>

        <div className="benchmark-chart-panel">
          <div className="benchmark-chart-header">
            <h3 className="chart-title">Benchmark Curve</h3>
            <button
              type="button"
              className="secondary-btn"
              onClick={() => setShowEditor((prev) => !prev)}
            >
              {showEditor ? "Hide Curve Editor" : "Adjust Curve"}
            </button>
          </div>

          <div className="benchmark-chart-wrapper">
            <div className="benchmark-axis-unit">{unit}</div>

            <ResponsiveContainer width="100%" height={340}>
              <LineChart
                data={chartData}
                margin={{ top: 30, right: 24, left: 24, bottom: 42 }}
              >
                <CartesianGrid stroke="#dce4e9" strokeDasharray="3 3" />
                <XAxis
                  dataKey="point"
                  tick={{ fontSize: 12 }}
                  interval="preserveStartEnd"
                >
                  <Label
                    value="Normalized Cycle Progress"
                    position="insideBottom"
                    offset={-18}
                    style={{ fontSize: 13 }}
                  />
                </XAxis>
                <YAxis tick={{ fontSize: 12 }}>
                  <Label
                    value="Pressure"
                    angle={-90}
                    position="insideLeft"
                    style={{ textAnchor: "middle", fontSize: 13 }}
                  />
                </YAxis>
                <Tooltip
                  formatter={(value) => [
                    `${Number(value).toFixed(3)} ${unit}`,
                    "Benchmark",
                  ]}
                  labelFormatter={(label) => `Point: ${label}`}
                />
                <Line
                  type="monotone"
                  dataKey="benchmark"
                  stroke="#2f6f8f"
                  strokeWidth={2}
                  dot={false}
                  name="benchmark"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {showEditor && (
        <EditableBenchmarkCurve
          benchmark={currentBenchmark}
          onCancel={() => setShowEditor(false)}
          onSaveSuccess={handleSaveSuccess}
        />
      )}
    </div>
  );
}
