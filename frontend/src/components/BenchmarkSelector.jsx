import React from "react";
import {
  enrichBenchmarksWithInferredDisplayContext,
  formatBenchmarkOptionLabel,
  getBenchmarkDisplayParts,
} from "../utils/sterilizerDisplay";
import { formatPlantDateTime } from "../utils/plantTime";

export default function BenchmarkSelector({
  benchmarks,
  selectedBenchmarkId,
  setSelectedBenchmarkId,
  onLoadBenchmark,
  loading,
}) {
  const displayBenchmarks = enrichBenchmarksWithInferredDisplayContext(benchmarks);
  const selectableBenchmarks = displayBenchmarks.filter(
    (item) => item.validation_status !== "invalid"
  );
  const selectedIsValid = selectableBenchmarks.some(
    (item) =>
      (item.benchmark_id || item.database_id || item.file_name) ===
      selectedBenchmarkId
  );

  return (
    <div className="benchmark-selector-container">
      <div className="benchmark-controls">
        <div className="field-group" style={{ marginBottom: 0, flex: 1 }}>
          <label>Select Benchmark File</label>
          <select
            value={selectedBenchmarkId}
            onChange={(e) => setSelectedBenchmarkId(e.target.value)}
            className="modern-select"
          >
            <option value="">-- Choose from library --</option>
            {selectableBenchmarks.map((item) => {
              const fileName = item.file_name || "";
              const identifier = item.benchmark_id || item.database_id || fileName;
              if (!fileName) return null;

              return (
                <option key={identifier} value={identifier}>
                  {formatBenchmarkOptionLabel(item, { includeFileName: true, benchmarkOptions: selectableBenchmarks })}
                </option>
              );
            })}
          </select>
        </div>

        <button
          onClick={onLoadBenchmark}
          disabled={loading || !selectedBenchmarkId || !selectedIsValid}
          className="btn-primary benchmark-load-button"
        >
          {loading ? "Loading..." : "Load Selected Benchmark"}
        </button>
      </div>

      <div className="table-responsive">
        <table className="benchmark-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Data ID</th>
              <th>Sterilizer</th>
              <th>Unit</th>
              <th>Cycles</th>
              <th>Created Date</th>
              <th>Status</th>
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
                const invalid = item.validation_status === "invalid";

                return (
                  <tr
                    key={item.benchmark_id || item.database_id || item.file_name}
                    className={`${selectedBenchmarkId === (item.benchmark_id || item.database_id || item.file_name) ? "selected-row" : ""} ${invalid ? "invalid-benchmark-row" : ""}`}
                  >
                    <td className="font-bold">{item.benchmark_name || "Unnamed"}</td>
                    <td className="text-muted" title={display.tagId || "No Data ID stored"}>
                      {display.tagShortName}
                    </td>
                    <td>
                      <span className="badge-light" title={display.sterilizerName}>
                        {display.sterilizerName}
                      </span>
                    </td>
                    <td>{item.unit || item.benchmark_unit || "bar"}</td>
                    <td className="text-center">{item.source_cycle_count ?? "-"}</td>
                    <td className="date-cell">
                      {item.created_at ? formatPlantDateTime(item.created_at) : "-"}
                    </td>
                    <td>
                      <span
                        className={`benchmark-validation-status ${invalid ? "invalid" : "valid"}`}
                        title={invalid ? item.validation_error || "Invalid benchmark file" : "Validated benchmark file"}
                      >
                        {invalid ? "Invalid" : "Valid"}
                      </span>
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
