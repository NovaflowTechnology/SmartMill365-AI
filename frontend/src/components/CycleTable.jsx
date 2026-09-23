import { Fragment, useId, useMemo, useState } from "react";
import { formatPlantDateTime, formatPlantTime } from "../utils/plantTime";
import { formatDurationHoursMinutes } from "../utils/duration";

function formatCycleTimestamp(value) {
  return formatPlantDateTime(value, {
    month: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
}

function formatChartTime(value) {
  return formatPlantTime(value);
}

function getCyclePoints(cycle, times, values) {
  const startMs = new Date(cycle.start).getTime();
  const endMs = new Date(cycle.end).getTime();

  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return [];

  return times.reduce((points, time, index) => {
    const timeMs = new Date(time).getTime();
    const value = Number(values[index]);

    if (
      Number.isFinite(timeMs) &&
      Number.isFinite(value) &&
      timeMs >= startMs &&
      timeMs <= endMs
    ) {
      points.push({ time: timeMs, value });
    }

    return points;
  }, []);
}

function CycleCurveSvg({ points, compact = false, unit = "" }) {
  const gradientId = useId().replaceAll(":", "");

  if (!points.length) return null;

  const width = compact ? 170 : 760;
  const height = compact ? 52 : 220;
  const padding = compact
    ? { top: 5, right: 5, bottom: 5, left: 5 }
    : { top: 18, right: 18, bottom: 34, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const minTime = Math.min(...points.map((point) => point.time));
  const maxTime = Math.max(...points.map((point) => point.time));
  const minValue = Math.min(...points.map((point) => point.value));
  const maxValue = Math.max(...points.map((point) => point.value));
  const timeRange = maxTime - minTime || 1;
  const valueRange = maxValue - minValue || 1;
  const coordinates = points.map((point, index) => {
    const x =
      points.length === 1
        ? padding.left + plotWidth / 2
        : padding.left + ((point.time - minTime) / timeRange) * plotWidth;
    const y =
      padding.top + plotHeight - ((point.value - minValue) / valueRange) * plotHeight;

    return { x, y, index };
  });
  const linePath = coordinates
    .map(
      (point) =>
        `${point.index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`
    )
    .join(" ");
  const areaPath = `${linePath} L${coordinates.at(-1).x.toFixed(2)},${(
    padding.top + plotHeight
  ).toFixed(2)} L${coordinates[0].x.toFixed(2)},${(
    padding.top + plotHeight
  ).toFixed(2)} Z`;
  return (
    <svg
      className={compact ? "cycle-curve-sparkline" : "cycle-curve-chart"}
      viewBox={`0 0 ${width} ${height}`}
      role={compact ? undefined : "img"}
      aria-label={
        compact
          ? undefined
          : `Detected cycle pressure curve${unit ? ` in ${unit}` : ""}`
      }
      aria-hidden={compact ? "true" : undefined}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2f839f" stopOpacity="0.24" />
          <stop offset="100%" stopColor="#2f839f" stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {!compact && (
        <>
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const y = padding.top + ratio * plotHeight;
            return (
              <line
                key={`horizontal-${ratio}`}
                x1={padding.left}
                x2={padding.left + plotWidth}
                y1={y}
                y2={y}
                className="cycle-curve-grid-line"
              />
            );
          })}
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const x = padding.left + ratio * plotWidth;
            return (
              <line
                key={`vertical-${ratio}`}
                x1={x}
                x2={x}
                y1={padding.top}
                y2={padding.top + plotHeight}
                className="cycle-curve-grid-line"
              />
            );
          })}
          <text x="8" y={padding.top + 4} className="cycle-curve-axis-text">
            {maxValue.toFixed(2)}
          </text>
          <text x="8" y={padding.top + plotHeight + 4} className="cycle-curve-axis-text">
            {minValue.toFixed(2)}
          </text>
          <text x={padding.left} y={height - 10} className="cycle-curve-axis-text">
            {formatChartTime(minTime)}
          </text>
          <text
            x={padding.left + plotWidth}
            y={height - 10}
            textAnchor="end"
            className="cycle-curve-axis-text"
          >
            {formatChartTime(maxTime)}
          </text>
          <text
            x={padding.left + plotWidth / 2}
            y={height - 10}
            textAnchor="middle"
            className="cycle-curve-axis-title"
          >
            Time
          </text>
          <text
            x="14"
            y={padding.top + plotHeight / 2}
            textAnchor="middle"
            transform={`rotate(-90 14 ${padding.top + plotHeight / 2})`}
            className="cycle-curve-axis-title"
          >
            Pressure{unit ? ` (${unit})` : ""}
          </text>
        </>
      )}

      <path d={areaPath} fill={`url(#${gradientId})`} />
      <path d={linePath} className="cycle-curve-line" />
    </svg>
  );
}

export default function CycleTable({
  cycles,
  selectedCycles,
  setSelectedCycles,
  times = [],
  values = [],
  sourceUnit = "",
}) {
  const [expandedCycle, setExpandedCycle] = useState(null);
  const unit = sourceUnit || cycles?.[0]?.source_unit || "";
  const cyclePoints = useMemo(
    () => cycles.map((cycle) => getCyclePoints(cycle, times, values)),
    [cycles, times, values]
  );

  const toggleCycle = (idx) => {
    if (selectedCycles.includes(idx)) {
      setSelectedCycles(selectedCycles.filter((x) => x !== idx));
    } else {
      setSelectedCycles([...selectedCycles, idx]);
    }
  };

  return (
    <div>
      <div className="table-wrapper">
        <table className="responsive-table cycle-summary-table">
          <thead>
            <tr>
              <th>Use</th>
              <th>Cycle No</th>
              <th className="cycle-curve-column">Cycle Curve</th>
              <th>Start Time</th>
              <th>End Time</th>
              <th>Duration</th>
              <th>Max{unit ? ` (${unit})` : ""}</th>
              <th>Min{unit ? ` (${unit})` : ""}</th>
            </tr>
          </thead>
          <tbody>
            {cycles.map((cycle, idx) => {
              const points = cyclePoints[idx] || [];
              const isExpanded = expandedCycle === idx;

              return (
                <Fragment key={`${cycle.cycle_no}-${cycle.start}-${cycle.end}`}>
                  <tr>
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedCycles.includes(idx)}
                        onChange={() => toggleCycle(idx)}
                      />
                    </td>
                    <td>{cycle.cycle_no}</td>
                    <td className="cycle-curve-cell">
                      {points.length ? (
                        <button
                          type="button"
                          className={`cycle-curve-toggle${isExpanded ? " is-expanded" : ""}`}
                          onClick={() => setExpandedCycle(isExpanded ? null : idx)}
                          aria-expanded={isExpanded}
                          aria-label={`${isExpanded ? "Hide" : "Expand"} pressure curve for cycle ${cycle.cycle_no}`}
                        >
                          <CycleCurveSvg points={points} compact unit={unit} />
                          <span>{isExpanded ? "Hide" : "Expand"}</span>
                        </button>
                      ) : (
                        <span className="cycle-curve-empty">No curve data</span>
                      )}
                    </td>
                    <td className="cell-wrap cycle-timestamp">
                      <time dateTime={cycle.start} title={cycle.start}>
                        {formatCycleTimestamp(cycle.start)}
                      </time>
                    </td>
                    <td className="cell-wrap cycle-timestamp">
                      <time dateTime={cycle.end} title={cycle.end}>
                        {formatCycleTimestamp(cycle.end)}
                      </time>
                    </td>
                    <td>{formatDurationHoursMinutes(cycle.duration_seconds)}</td>
                    <td>{Number(cycle.max_value).toFixed(3)}</td>
                    <td>{Number(cycle.min_value).toFixed(3)}</td>
                  </tr>

                  {isExpanded && points.length > 0 && (
                    <tr className="cycle-curve-detail-row">
                      <td colSpan="8">
                        <div className="cycle-curve-detail">
                          <div className="cycle-curve-detail-header">
                            <div>
                              <strong>Cycle {cycle.cycle_no} Pressure Curve</strong>
                              <span>
                                {formatCycleTimestamp(cycle.start)} – {formatCycleTimestamp(cycle.end)}
                              </span>
                            </div>
                            <span>{points.length} data points</span>
                          </div>
                          <CycleCurveSvg points={points} unit={unit} />
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
