import { useEffect, useMemo, useRef, useState } from "react";
import api from "../api";
import NotificationToast from "./NotificationToast";

const SVG_WIDTH = 980;
const SVG_HEIGHT = 420;
const MARGIN = { top: 42, right: 26, bottom: 58, left: 78 };
const PLOT_WIDTH = SVG_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = SVG_HEIGHT - MARGIN.top - MARGIN.bottom;

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function smoothArray(values, iterations = 1) {
  let arr = [...values];
  for (let k = 0; k < iterations; k += 1) {
    const next = [...arr];
    for (let i = 1; i < arr.length - 1; i += 1) {
      next[i] = 0.25 * arr[i - 1] + 0.5 * arr[i] + 0.25 * arr[i + 1];
    }
    arr = next;
  }
  return arr;
}

function percentile(values, p) {
  if (!values || values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = (sorted.length - 1) * p;
  const lower = Math.floor(idx);
  const upper = Math.ceil(idx);
  if (lower === upper) return sorted[lower];
  const ratio = idx - lower;
  return sorted[lower] * (1 - ratio) + sorted[upper] * ratio;
}

function argMaxInRange(values, start, end) {
  const s = clamp(start, 0, values.length - 1);
  const e = clamp(end, s, values.length - 1);
  let bestIdx = s;
  for (let i = s + 1; i <= e; i += 1) {
    if (values[i] > values[bestIdx]) bestIdx = i;
  }
  return bestIdx;
}

function argMinInRange(values, start, end) {
  const s = clamp(start, 0, values.length - 1);
  const e = clamp(end, s, values.length - 1);
  let bestIdx = s;
  for (let i = s + 1; i <= e; i += 1) {
    if (values[i] < values[bestIdx]) bestIdx = i;
  }
  return bestIdx;
}

function buildTicks(maxValue) {
  const tickCount = 5;
  const ticks = [];
  for (let i = 0; i < tickCount; i += 1) {
    ticks.push((maxValue / (tickCount - 1)) * i);
  }
  return ticks;
}

function gaussianWeight(distance, sigma) {
  return Math.exp(-0.5 * Math.pow(distance / Math.max(sigma, 1), 2));
}

function detectProcessAnchors(curve) {
  const n = curve.length;
  if (!n) return [];

  const smoothed = smoothArray(curve, 5);
  const maxVal = Math.max(...smoothed);
  const minVal = Math.min(...smoothed);
  const range = Math.max(maxVal - minVal, 1e-6);

  const plateauThreshold = minVal + 0.82 * range;
  let maxLen = 0;
  let currentLen = 0;
  let pStart = 0;
  let bestPStart = Math.floor(n * 0.4);
  let bestPEnd = Math.floor(n * 0.8);

  for (let i = 0; i < n; i += 1) {
    if (smoothed[i] > plateauThreshold) {
      if (currentLen === 0) pStart = i;
      currentLen += 1;
      if (currentLen > maxLen) {
        maxLen = currentLen;
        bestPStart = pStart;
        bestPEnd = i;
      }
    } else {
      currentLen = 0;
    }
  }

  const plateauStart = bestPStart;
  const plateauEnd = bestPEnd;
  const holdLevel = Math.floor((plateauStart + plateauEnd) / 2);

  const localMaxes = [];
  const prePlateauEnd = Math.max(10, plateauStart - Math.floor(n * 0.01));

  for (let i = 5; i < prePlateauEnd; i += 1) {
    let isMax = true;
    for (let j = 1; j <= 5; j += 1) {
      if (smoothed[i] < smoothed[i - j] || smoothed[i] < smoothed[i + j]) {
        isMax = false;
        break;
      }
    }
    if (isMax && smoothed[i] > minVal + 0.1 * range) {
      if (localMaxes.length > 0 && i - localMaxes[localMaxes.length - 1] < 12) {
        if (smoothed[i] > smoothed[localMaxes[localMaxes.length - 1]]) {
          localMaxes[localMaxes.length - 1] = i;
        }
      } else {
        localMaxes.push(i);
      }
    }
  }

  let firstPeak;
  let secondPeak;

  if (localMaxes.length >= 2) {
    firstPeak = localMaxes[0];
    secondPeak = localMaxes[localMaxes.length - 1];
  } else if (localMaxes.length === 1) {
    firstPeak = localMaxes[0];
    secondPeak = Math.floor((firstPeak + plateauStart) / 2);
  } else {
    firstPeak = Math.floor(plateauStart * 0.33);
    secondPeak = Math.floor(plateauStart * 0.66);
  }

  let firstValley = argMinInRange(smoothed, firstPeak + 4, secondPeak - 4);
  if (firstValley <= firstPeak || firstValley >= secondPeak) {
    firstValley = Math.floor((firstPeak + secondPeak) / 2);
  }

  let secondValley = argMinInRange(smoothed, secondPeak + 4, plateauStart - 2);
  if (secondValley <= secondPeak || secondValley >= plateauStart) {
    secondValley = Math.floor((secondPeak + plateauStart) / 2);
  }

  let dropStart = plateauEnd;
  for (let i = Math.floor((holdLevel + plateauEnd) / 2); i < n - 5; i += 1) {
    if (smoothed[i] - smoothed[i + 5] > 0.03 * range) {
      dropStart = i;
      break;
    }
  }

  let dropEnd = argMinInRange(smoothed, dropStart + 5, n - 1);
  if (dropEnd <= dropStart + 5) dropEnd = n - 1;

  const anchors = [
    {
      id: "first_peak",
      label: "First Peak",
      type: "peak",
      index: firstPeak,
      influenceWidth: Math.max(10, Math.floor(n * 0.045)),
      maxShiftX: Math.max(8, Math.floor(n * 0.03)),
    },
    {
      id: "first_valley",
      label: "First Valley",
      type: "valley",
      index: firstValley,
      influenceWidth: Math.max(10, Math.floor(n * 0.045)),
      maxShiftX: Math.max(8, Math.floor(n * 0.03)),
    },
    {
      id: "second_peak",
      label: "Second Peak",
      type: "peak",
      index: secondPeak,
      influenceWidth: Math.max(12, Math.floor(n * 0.05)),
      maxShiftX: Math.max(10, Math.floor(n * 0.035)),
    },
    {
      id: "second_valley",
      label: "Second Valley",
      type: "valley",
      index: secondValley,
      influenceWidth: Math.max(10, Math.floor(n * 0.045)),
      maxShiftX: Math.max(8, Math.floor(n * 0.03)),
    },
    {
      id: "hold_level",
      label: "Hold Level",
      type: "hold",
      index: holdLevel,
      influenceWidth: Math.max(24, Math.floor(n * 0.11)),
      maxShiftX: Math.max(16, Math.floor(n * 0.06)),
    },
    {
      id: "drop_start",
      label: "Drop Start",
      type: "drop_start",
      index: dropStart,
      influenceWidth: Math.max(18, Math.floor(n * 0.08)),
      maxShiftX: Math.max(14, Math.floor(n * 0.05)),
    },
    {
      id: "drop_end",
      label: "Drop End",
      type: "drop_end",
      index: dropEnd,
      influenceWidth: Math.max(14, Math.floor(n * 0.06)),
      maxShiftX: Math.max(10, Math.floor(n * 0.035)),
    },
  ];

  const minGap = Math.max(8, Math.floor(n * 0.02));
  anchors.sort((a, b) => a.index - b.index);

  for (let i = 1; i < anchors.length; i += 1) {
    if (anchors[i].index <= anchors[i - 1].index + minGap) {
      anchors[i].index = anchors[i - 1].index + minGap;
    }
  }

  return anchors.map((a) => ({
    ...a,
    index: clamp(a.index, 1, n - 1),
    baseValue: curve[clamp(a.index, 1, n - 1)],
    delta: 0,
    deltaX: 0,
  }));
}

function rebuildCurveWith2DDeformation(originalCurve, anchors) {
  if (!originalCurve.length) return [];
  const n = originalCurve.length;

  const warpedPoints = [];

  for (let i = 0; i < n; i += 1) {
    let shiftX = 0;
    let shiftY = 0;

    anchors.forEach((anchor) => {
      const { index, delta, deltaX = 0, influenceWidth, id } = anchor;
      if (!delta && !deltaX) return;

      let leftMultiplier = 3;
      let rightMultiplier = 3;

      if (id === "drop_start") {
        leftMultiplier = 1.5;
        rightMultiplier = 4;
      }
      if (id === "drop_end") {
        leftMultiplier = 2.5;
        rightMultiplier = 1.2;
      }
      if (id === "hold_level") {
        leftMultiplier = 3.5;
        rightMultiplier = 3.5;
      }

      const distance = i - index;

      if (distance < 0 && Math.abs(distance) > influenceWidth * leftMultiplier) return;
      if (distance > 0 && Math.abs(distance) > influenceWidth * rightMultiplier) return;

      const weight = gaussianWeight(Math.abs(distance), influenceWidth);
      shiftX += deltaX * weight;
      shiftY += delta * weight;
    });

    warpedPoints.push({
      x: clamp(i + shiftX, 0, n - 1),
      y: Math.max(0, originalCurve[i] + shiftY),
    });
  }

  for (let i = 1; i < n; i += 1) {
    if (warpedPoints[i].x <= warpedPoints[i - 1].x) {
      warpedPoints[i].x = warpedPoints[i - 1].x + 0.001;
    }
  }

  const resampled = new Array(n).fill(0);
  let warpIdx = 0;

  for (let targetX = 0; targetX < n; targetX += 1) {
    while (warpIdx < n - 2 && warpedPoints[warpIdx + 1].x < targetX) {
      warpIdx += 1;
    }

    const p1 = warpedPoints[warpIdx];
    const p2 = warpedPoints[warpIdx + 1] || p1;

    if (p1.x === p2.x) {
      resampled[targetX] = p1.y;
    } else {
      const ratio = (targetX - p1.x) / (p2.x - p1.x);
      resampled[targetX] = p1.y + ratio * (p2.y - p1.y);
    }
  }

  return smoothArray(resampled, 1);
}

function interpolateCurveValue(curve, indexFloat) {
  if (!curve.length) return 0;
  const left = Math.floor(indexFloat);
  const right = Math.ceil(indexFloat);

  if (left === right) {
    return curve[clamp(left, 0, curve.length - 1)];
  }

  const l = clamp(left, 0, curve.length - 1);
  const r = clamp(right, 0, curve.length - 1);
  const ratio = indexFloat - left;
  return curve[l] * (1 - ratio) + curve[r] * ratio;
}

function findHoldIndex(curve, start, end) {
  const s = clamp(Math.floor(start), 0, curve.length - 1);
  const e = clamp(Math.floor(end), s, curve.length - 1);

  const local = curve.slice(s, e + 1);
  const highRef = percentile(local, 0.85);

  let bestIdx = s;
  let bestScore = -Infinity;

  for (let i = s; i <= e; i += 1) {
    const left = clamp(i - 3, 0, curve.length - 1);
    const right = clamp(i + 3, 0, curve.length - 1);
    const slope = Math.abs(curve[right] - curve[left]);
    const score = curve[i] - 0.8 * slope + (curve[i] >= highRef ? 0.2 : 0);

    if (score > bestScore) {
      bestScore = score;
      bestIdx = i;
    }
  }

  return bestIdx;
}

function findDropStartIndex(curve, start, end) {
  const s = clamp(Math.floor(start), 0, curve.length - 2);
  const e = clamp(Math.floor(end), s + 1, curve.length - 2);

  let bestIdx = s;
  let bestDrop = -Infinity;

  for (let i = s; i <= e; i += 1) {
    const right = clamp(i + 5, 0, curve.length - 1);
    const drop = curve[i] - curve[right];
    if (drop > bestDrop) {
      bestDrop = drop;
      bestIdx = i;
    }
  }

  return bestIdx;
}

function snapAnchorsToFeatures(anchors, editedCurve, n) {
  if (!anchors.length || !editedCurve.length) return [];

  const sorted = [...anchors].sort((a, b) => a.index + a.deltaX - (b.index + b.deltaX));
  const snapped = [];
  const minGap = Math.max(8, Math.floor(n * 0.02));

  for (let i = 0; i < sorted.length; i += 1) {
    const anchor = sorted[i];
    const predictedIndex = clamp(anchor.index + (anchor.deltaX || 0), 1, n - 1);
    const radius = Math.max(6, Math.floor(anchor.influenceWidth * 0.9));

    let searchStart = clamp(predictedIndex - radius, 1, n - 1);
    let searchEnd = clamp(predictedIndex + radius, 1, n - 1);

    if (snapped.length > 0) {
      searchStart = Math.max(searchStart, snapped[snapped.length - 1].snappedIndex + minGap);
    }

    if (i < sorted.length - 1) {
      const nextPredicted = clamp(
        sorted[i + 1].index + (sorted[i + 1].deltaX || 0),
        1,
        n - 1
      );
      searchEnd = Math.min(searchEnd, nextPredicted - minGap);
    }

    if (searchEnd <= searchStart) {
      searchEnd = Math.min(n - 1, searchStart + minGap);
    }

    let snappedIndex = predictedIndex;

    if (anchor.type === "peak") {
      snappedIndex = argMaxInRange(editedCurve, searchStart, searchEnd);
    } else if (anchor.type === "valley") {
      snappedIndex = argMinInRange(editedCurve, searchStart, searchEnd);
    } else if (anchor.type === "hold") {
      snappedIndex = findHoldIndex(editedCurve, searchStart, searchEnd);
    } else if (anchor.type === "drop_start") {
      snappedIndex = findDropStartIndex(editedCurve, searchStart, searchEnd);
    } else if (anchor.type === "drop_end") {
      snappedIndex = argMinInRange(editedCurve, searchStart, searchEnd);
    }

    snapped.push({
      ...anchor,
      snappedIndex,
      snappedValue: interpolateCurveValue(editedCurve, snappedIndex),
    });
  }

  return snapped;
}

export default function EditableBenchmarkCurve({
  benchmark,
  onCancel,
  onSaveSuccess,
}) {
  const originalCurve = useMemo(
    () => benchmark?.benchmark_curve || [],
    [benchmark]
  );

  const [anchors, setAnchors] = useState([]);
  const [editedCurve, setEditedCurve] = useState([]);
  const [saveName, setSaveName] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState("info");
  const [messageAutoDismissMs, setMessageAutoDismissMs] = useState(3000);
  const [draggingId, setDraggingId] = useState(null);

  const svgRef = useRef(null);

  useEffect(() => {
    if (!originalCurve.length) return;

    const initialAnchors = detectProcessAnchors(originalCurve);
    setAnchors(initialAnchors);
    setEditedCurve(rebuildCurveWith2DDeformation(originalCurve, initialAnchors));
    setSaveName(`${benchmark?.benchmark_name || "benchmark"}_adjusted`);
    setMessage("");
  }, [originalCurve, benchmark]);

  useEffect(() => {
    if (!originalCurve.length || !anchors.length) return;
    setEditedCurve(rebuildCurveWith2DDeformation(originalCurve, anchors));
  }, [anchors, originalCurve]);

  const unit = benchmark?.unit || "bar";

  const yMax = useMemo(() => {
    const maxOriginal = originalCurve.length ? Math.max(...originalCurve) : 1;
    const maxEdited = editedCurve.length ? Math.max(...editedCurve) : 1;
    return Math.max(1, Math.max(maxOriginal, maxEdited) * 1.15);
  }, [originalCurve, editedCurve]);

  const yTicks = useMemo(() => buildTicks(yMax), [yMax]);

  const xToSvg = (index) =>
    MARGIN.left + (index / Math.max(originalCurve.length - 1, 1)) * PLOT_WIDTH;

  const svgToIndex = (svgX) => {
    const plotX = clamp(svgX - MARGIN.left, 0, PLOT_WIDTH);
    return (plotX / PLOT_WIDTH) * Math.max(originalCurve.length - 1, 1);
  };

  const yToSvg = (value) =>
    MARGIN.top + (1 - value / yMax) * PLOT_HEIGHT;

  const svgToYValue = (svgY) => {
    const plotY = clamp(svgY - MARGIN.top, 0, PLOT_HEIGHT);
    return ((PLOT_HEIGHT - plotY) / PLOT_HEIGHT) * yMax;
  };

  const buildPath = (curve) => {
    if (!curve.length) return "";
    return curve
      .map((v, i) => `${i === 0 ? "M" : "L"} ${xToSvg(i)} ${yToSvg(v)}`)
      .join(" ");
  };

  const snappedAnchors = useMemo(
    () => snapAnchorsToFeatures(anchors, editedCurve, originalCurve.length),
    [anchors, editedCurve, originalCurve.length]
  );

  const handlePointerMove = (event) => {
    if (!draggingId || !svgRef.current) return;

    const rect = svgRef.current.getBoundingClientRect();
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;

    const nextValue = clamp(svgToYValue(pointerY), 0, yMax);
    const nextIndex = svgToIndex(pointerX);

    setAnchors((prev) => {
      const current = prev.find((a) => a.id === draggingId);
      if (!current) return prev;

      const currentIdx = prev.findIndex((a) => a.id === draggingId);
      const prevAnchor = prev[currentIdx > 0 ? currentIdx - 1 : currentIdx];
      const nextAnchor = prev[currentIdx < prev.length - 1 ? currentIdx + 1 : currentIdx];
      const minGap = Math.max(8, Math.floor(originalCurve.length * 0.02));

      let proposedDeltaX = nextIndex - current.index;
      proposedDeltaX = clamp(proposedDeltaX, -current.maxShiftX, current.maxShiftX);

      let proposedIndex = current.index + proposedDeltaX;

      if (currentIdx > 0) {
        const prevIndex = prevAnchor.index + (prevAnchor.deltaX || 0);
        proposedIndex = Math.max(proposedIndex, prevIndex + minGap);
      }

      if (currentIdx < prev.length - 1) {
        const nextIndexAnchor = nextAnchor.index + (nextAnchor.deltaX || 0);
        proposedIndex = Math.min(proposedIndex, nextIndexAnchor - minGap);
      }

      proposedIndex = clamp(proposedIndex, 1, originalCurve.length - 1);

      return prev.map((anchor) =>
        anchor.id === draggingId
          ? {
              ...anchor,
              delta: nextValue - anchor.baseValue,
              deltaX: proposedIndex - anchor.index,
            }
          : anchor
      );
    });
  };

  const stopDragging = () => setDraggingId(null);

  const showMessage = (text, tone = "info", autoDismissMs = 3000) => {
    setMessageTone(tone);
    setMessageAutoDismissMs(autoDismissMs);
    setMessage(text);
  };

  useEffect(() => {
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopDragging);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopDragging);
    };
  });

  const handleReset = () => {
    const initialAnchors = detectProcessAnchors(originalCurve);
    setAnchors(initialAnchors);
    setEditedCurve(rebuildCurveWith2DDeformation(originalCurve, initialAnchors));
    showMessage("Curve reset to original benchmark.");
  };

  const handleSmooth = () => {
    const smoothed = smoothArray(editedCurve, 2);
    setEditedCurve(smoothed);

    const snappedOnSmoothed = snapAnchorsToFeatures(anchors, smoothed, originalCurve.length);

    setAnchors((prev) =>
      prev.map((anchor) => {
        const snapped = snappedOnSmoothed.find((s) => s.id === anchor.id);
        const nextIndex = snapped ? Math.round(snapped.snappedIndex) : anchor.index;
        return {
          ...anchor,
          index: nextIndex,
          baseValue: interpolateCurveValue(smoothed, nextIndex),
          delta: 0,
          deltaX: 0,
        };
      })
    );

    showMessage("Smoothing applied.");
  };

  const handleSave = async () => {
    if (!saveName.trim()) {
      showMessage("Please enter a benchmark name before saving.", "error", 0);
      return;
    }

    try {
      setSaving(true);
      setMessage("");

      const sourceTagId =
        benchmark?.tag_id ||
        benchmark?.sterilizer_id ||
        benchmark?.id ||
        benchmark?.tagId ||
        benchmark?.sterilizerId ||
        null;

      const payload = {
        ...benchmark,
        benchmark_name: saveName.trim(),
        benchmark_curve: editedCurve,
        field: benchmark?.field || benchmark?.channel || null,
        id: sourceTagId,
        tag_id: sourceTagId,
        sterilizer_id: sourceTagId,
        anchor_indices: snappedAnchors.map((a) => Math.round(a.snappedIndex)),
        anchor_values: snappedAnchors.map((a) => a.snappedValue),
        adjusted_from:
          benchmark?.benchmark_id || benchmark?.database_id || null,
        adjusted_from_file_name: benchmark?.file_name || null,
      };

      const res = await api.post("/api/benchmarks/save-adjusted", payload);

      showMessage("Adjusted benchmark saved successfully.", "success");
      if (onSaveSuccess) {
        onSaveSuccess(res.data);
      }
    } catch (err) {
      console.error("Adjusted benchmark save error:", err);
      const message =
        err?.response?.status && err.response.status < 500
          ? err.response.data?.detail || "The adjusted benchmark could not be saved. Check the benchmark name and try again."
          : "The adjusted benchmark could not be saved. Please try again.";
      showMessage(message, "error", 0);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="editable-benchmark-editor">
      <div className="editor-topbar">
        <div>
          <h3 className="editor-title">Adjust Benchmark Curve</h3>
          <p className="editor-subtitle">
            Drag the anchor points horizontally and vertically to adjust peak,
            valley, holding, and drop regions while preserving the original curve shape.
          </p>
        </div>

        <div className="editor-actions">
          <button type="button" className="secondary-btn" onClick={handleReset}>
            Reset
          </button>
          <button type="button" className="secondary-btn" onClick={handleSmooth}>
            Apply Smoothing
          </button>
          <button type="button" className="secondary-btn" onClick={onCancel}>
            Close
          </button>
        </div>
      </div>

      <div className="editor-save-row">
        <div className="field-group">
          <label>New Benchmark Name</label>
          <input
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            className="benchmark-name-input"
            placeholder="Enter adjusted benchmark name"
          />
        </div>

        <div className="editor-save-btn-wrap">
          <button type="button" onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save as New Benchmark"}
          </button>
        </div>
      </div>

      <NotificationToast
        message={message}
        tone={messageTone}
        autoDismissMs={messageAutoDismissMs}
        onClose={() => setMessage("")}
      />

      <div className="editable-chart-shell">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
          className="editable-benchmark-svg"
        >
          <text x={MARGIN.left} y={22} className="svg-unit-label">
            {unit}
          </text>

          <text
            x={24}
            y={MARGIN.top + PLOT_HEIGHT / 2}
            transform={`rotate(-90, 24, ${MARGIN.top + PLOT_HEIGHT / 2})`}
            className="svg-axis-label"
          >
            Pressure
          </text>

          <text
            x={MARGIN.left + PLOT_WIDTH / 2}
            y={SVG_HEIGHT - 12}
            textAnchor="middle"
            className="svg-axis-label"
          >
            Normalized Cycle Progress
          </text>

          {yTicks.map((tick, idx) => {
            const y = yToSvg(tick);
            return (
              <g key={`ytick-${idx}`}>
                <line
                  x1={MARGIN.left}
                  y1={y}
                  x2={MARGIN.left + PLOT_WIDTH}
                  y2={y}
                  className="svg-grid-line"
                />
                <line
                  x1={MARGIN.left - 6}
                  y1={y}
                  x2={MARGIN.left}
                  y2={y}
                  className="svg-axis-line"
                />
                <text
                  x={MARGIN.left - 10}
                  y={y + 4}
                  textAnchor="end"
                  className="svg-tick-text"
                >
                  {Number(tick).toFixed(1).replace(/\.0$/, "")}
                </text>
              </g>
            );
          })}

          {Array.from({ length: 12 }).map((_, idx) => {
            const pointIdx = Math.round(
              (idx / 11) * Math.max(originalCurve.length - 1, 1)
            );
            const x = xToSvg(pointIdx);
            return (
              <g key={`xtick-${idx}`}>
                <line
                  x1={x}
                  y1={MARGIN.top}
                  x2={x}
                  y2={MARGIN.top + PLOT_HEIGHT}
                  className="svg-grid-line"
                />
                <line
                  x1={x}
                  y1={MARGIN.top + PLOT_HEIGHT}
                  x2={x}
                  y2={MARGIN.top + PLOT_HEIGHT + 6}
                  className="svg-axis-line"
                />
                <text
                  x={x}
                  y={MARGIN.top + PLOT_HEIGHT + 20}
                  textAnchor="middle"
                  className="svg-tick-text"
                >
                  {pointIdx + 1}
                </text>
              </g>
            );
          })}

          <line
            x1={MARGIN.left}
            y1={MARGIN.top + PLOT_HEIGHT}
            x2={MARGIN.left + PLOT_WIDTH}
            y2={MARGIN.top + PLOT_HEIGHT}
            className="svg-axis-line"
          />
          <line
            x1={MARGIN.left}
            y1={MARGIN.top}
            x2={MARGIN.left}
            y2={MARGIN.top + PLOT_HEIGHT}
            className="svg-axis-line"
          />

          <path d={buildPath(originalCurve)} className="svg-original-curve" />
          <path d={buildPath(editedCurve)} className="svg-edited-curve" />

          {snappedAnchors.map((anchor) => (
          <g key={anchor.id}>
              <circle
                cx={xToSvg(anchor.snappedIndex)}
                cy={yToSvg(anchor.snappedValue)}
                r={7}
                className="svg-anchor-point"
                onPointerDown={(e) => {
                    e.preventDefault();
                    setDraggingId(anchor.id);
                }}
              />
          </g>
        ))}
        </svg>
      </div>

      <div className="editor-legend">
        <span>
          <span className="legend-swatch original" /> Original Benchmark
        </span>
        <span>
          <span className="legend-swatch edited" /> Edited Benchmark
        </span>
        <span>
          <span className="legend-swatch anchor" /> Draggable Anchor
        </span>
      </div>
    </div>
  );
}
