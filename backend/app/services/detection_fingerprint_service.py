"""Deterministic fingerprints for Detect Cycles -> Build Benchmark handoff."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Tuple


def cycle_boundaries(df, cycles_idx: Iterable[Tuple[int, int]]) -> List[Dict[str, str]]:
    return [
        {
            "start": df.iloc[start]["time"].isoformat(),
            "end": df.iloc[end]["time"].isoformat(),
        }
        for start, end in cycles_idx
    ]


def _cycle_signatures(df, cycles_idx) -> List[Dict[str, Any]]:
    value_column = (
        "value_std"
        if "value_std" in df.columns
        else "value"
        if "value" in df.columns
        else "smooth"
    )
    output = []
    for start, end in cycles_idx:
        cycle = df.iloc[start : end + 1]
        samples = [
            [timestamp.isoformat(), round(float(value), 9)]
            for timestamp, value in zip(cycle["time"], cycle[value_column])
        ]
        sample_hash = hashlib.sha256(
            json.dumps(samples, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        output.append(
            {
                "start": cycle.iloc[0]["time"].isoformat(),
                "end": cycle.iloc[-1]["time"].isoformat(),
                "points": int(len(cycle)),
                "sample_hash": sample_hash,
            }
        )
    return output


def build_detection_fingerprint(
    *,
    bucket: str,
    measurement: str,
    field: str,
    tag_id: str,
    source_unit: str,
    start_time: str,
    stop_time: str,
    smooth_window: int,
    df,
    cycles_idx,
) -> str:
    document = {
        "bucket": str(bucket),
        "measurement": str(measurement),
        "field": str(field),
        "tag_id": str(tag_id),
        "source_unit": str(source_unit),
        "start_time": str(start_time),
        "stop_time": str(stop_time),
        "smooth_window": int(smooth_window),
        "cycles": _cycle_signatures(df, cycles_idx),
    }
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def selected_cycle_indices_from_boundaries(
    df,
    cycles_idx,
    selected_boundaries: Iterable[Any],
) -> List[int]:
    available = cycle_boundaries(df, cycles_idx)
    lookup = {
        (item["start"], item["end"]): index
        for index, item in enumerate(available)
    }
    selected_indices: List[int] = []
    seen = set()
    for selection in selected_boundaries or []:
        if hasattr(selection, "dict"):
            selection = selection.dict()
        start = str((selection or {}).get("start") or "").strip()
        end = str((selection or {}).get("end") or "").strip()
        key = (start, end)
        if not start or not end or key not in lookup:
            raise ValueError(
                "Detected cycles changed or the selection is no longer valid. "
                "Run Detect Cycles again before building the benchmark."
            )
        selected_index = lookup[key]
        if selected_index in seen:
            continue
        seen.add(selected_index)
        selected_indices.append(selected_index)
    if not selected_indices:
        raise ValueError("Select at least one complete cycle before building the benchmark.")
    return selected_indices
