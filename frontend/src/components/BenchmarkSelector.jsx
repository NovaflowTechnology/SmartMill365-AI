import React from "react";
import {
  enrichBenchmarksWithInferredDisplayContext,
  formatBenchmarkOptionLabel,
  getBenchmarkDisplayParts,
} from "../utils/sterilizerDisplay";

export default function BenchmarkSelector({
  benchmarks,
  selectedBenchmarkFile,
  setSelectedBenchmarkFile,
  onLoadBenchmark,
  loading,
}) {
  const displayBenchmarks = enrichBenchmarksWithInferredDisplayContext(benchmarks);

  return (
    <div className="benchmark-selector-container">
      <div className="benchmark-controls">
        <div className="field-group" style={{ marginBottom: 0, flex: 1 }}>
          <label>Select Benchmark File</label>
          <select
            value={selectedBenchmarkFile}
            onChange={(e) => setSelectedBenchmarkFile(e.target.value)}
            className="modern-select"
          >
            <option value="">-- Choose from library --</option>
            {displayBenchmarks.map((item) => {
              const fileName = item.file_name || "";
              if (!fileName) return null;

              return (
                <option key={fileName} value={fileName}>
                  {formatBenchmarkOptionLabel(item, { includeFileName: false, benchmarkOptions: displayBenchmarks })}
                </option>
              );
            })}
          </select>
        </div>

        <button
          onClick={onLoadBenchmark}
          disabled={loading || !selectedBenchmarkFile}
          className="btn-primary"
          style={{ height: "42px" }}
        >
          {loading ? "Loading..." : "Load Selected Benchmark"}
        </button>
      </div>

      <div className="table-responsive">
        <table className="benchmark-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Mill / Tag</th>
              <th>Sterilizer</th>
              <th>Unit</th>
              <th>Cycles</th>
              <th>Points</th>
              <th>Created Date</th>
            </tr>
          </thead>

          <tbody>
            {displayBenchmarks.length === 0 ? (
              <tr>
                <td colSpan="7" style={{ textAlign: "center", padding: "20px" }}>
                  No benchmarks found.
                </td>
              </tr>
            ) : (
              displayBenchmarks.map((item) => {
                const display = getBenchmarkDisplayParts(item, displayBenchmarks);

                return (
                  <tr
                    key={item.file_name}
                    className={selectedBenchmarkFile === item.file_name ? "selected-row" : ""}
                  >
                    <td className="font-bold">{item.benchmark_name || "Unnamed"}</td>
                    <td className="text-muted" title={display.tagId || "No Tag ID stored"}>
                      {display.tagShortName}
                    </td>
                    <td>
                      <span className="badge-light" title={display.field || "No field stored"}>
                        {display.sterilizerName}
                      </span>
                    </td>
                    <td>{item.unit || item.benchmark_unit || "bar"}</td>
                    <td className="text-center">{item.source_cycle_count ?? "-"}</td>
                    <td className="text-center">{item.normalized_points ?? "-"}</td>
                    <td className="date-cell">
                      {item.created_at ? item.created_at.replace("T", " ").slice(0, 16) : "-"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
