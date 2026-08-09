import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

function scoreColor(score) {
  if (score >= 85) return "#22c55e";
  if (score >= 65) return "#f59e0b";
  return "#ef4444";
}

export default function ComparisonResults({ comparisonResult }) {
  const cycleResults = comparisonResult?.cycle_results || [];

  if (!cycleResults.length) {
    return (
      <div>
        <h2>Comparison Results</h2>
        <p>No cycle comparison results available.</p>
      </div>
    );
  }

  return (
    <div>
      <h2>Comparison Results</h2>

      <div className="info-grid" style={{ marginBottom: 20 }}>
        <div><strong>Benchmark Name:</strong> {comparisonResult.benchmark_name}</div>
        <div><strong>Benchmark ID:</strong> {comparisonResult.benchmark_id}</div>
        <div><strong>Benchmark Field:</strong> {comparisonResult.benchmark_field}</div>
        <div><strong>Benchmark Unit:</strong> {comparisonResult.benchmark_unit}</div>
      </div>

      {cycleResults.map((cycle) => (
        <div
          key={cycle.cycle_no}
          style={{
            border: "1px solid rgba(148,163,184,0.2)",
            borderRadius: 12,
            padding: 16,
            marginBottom: 24,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
            <h3 style={{ margin: 0 }}>Cycle {cycle.cycle_no}</h3>
            <div
              style={{
                fontWeight: 700,
                color: scoreColor(cycle.score),
                fontSize: 18,
              }}
            >
              Score: {cycle.score}%
            </div>
          </div>

          <p><strong>Classification:</strong> {cycle.classification}</p>
          <p><strong>Start:</strong> {cycle.cycle_start}</p>
          <p><strong>End:</strong> {cycle.cycle_end}</p>
          <p><strong>Duration:</strong> {cycle.duration_seconds.toFixed(1)} s</p>

          <h4>System Feedback & Diagnostics</h4>
          <div style={{ background: "#f8fafc", padding: "12px", borderRadius: "8px", border: "1px solid #e2e8f0" }}>
            {cycle.feedback_messages.map((msg, idx) => (
              <div key={idx} style={{ marginBottom: "8px", fontSize: "0.9rem", display: "flex", gap: "8px" }}>
                <span style={{ color: scoreColor(cycle.score) }}>•</span>
                <span>{msg}</span>
              </div>
            ))}
          </div>

          <h4>Real-Time Curve Overlay with Benchmark</h4>
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={380}>
              <LineChart data={cycle.visual_overlay_chart}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="time" hide />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="realtime_smooth" dot={false} name="Real-Time" />
                <Line type="monotone" dataKey="benchmark" dot={false} name="Benchmark" />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <h4 style={{ marginTop: 20 }}>Score Breakdown</h4>
          <div className="table-wrapper">
            <table className="responsive-table">
              <thead>
                <tr>
                  <th>Shape</th>
                  <th>Duration</th>
                  <th>Peak Timing</th>
                  <th>Peak Value</th>
                  <th>Peak Count</th>
                  <th>Stretch Ratio</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{cycle.score_breakdown.shape_score}</td>
                  <td>{cycle.score_breakdown.duration_score}</td>
                  <td>{cycle.score_breakdown.peak_timing_score}</td>
                  <td>{cycle.score_breakdown.peak_value_score}</td>
                  <td>{cycle.score_breakdown.peak_count_score}</td>
                  <td>{cycle.score_breakdown.stretch_ratio_score}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}