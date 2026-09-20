"""Benchmark generation, validation, and Supabase persistence."""

from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import numpy as np

from app.config import BENCHMARK_UNIT
from app.services.audit_service import try_record_audit_event
from app.services.supabase_service import get_supabase_client
from app.services.unit_service import measurement_for_unit, normalize_unit_name


BENCHMARK_TABLE = "sterilizer_benchmarks"
EQUIPMENT_TABLE = "equipment"
BENCHMARK_SELECT_COLUMNS = ",".join(
    [
        "id",
        "equipment_id",
        "channel_id",
        "name",
        "start_time",
        "stop_time",
        "curve",
        "std",
        "normalized_points",
        "source_cycle_count",
        "created_at",
        "measurement",
        "field",
        "tag_id",
        "source_unit",
        "benchmark_unit",
        "conversion_applied",
        "value_column_used",
        "source_cycles",
        "file_name",
        "adjusted_from",
        "adjusted_from_file_name",
        "anchor_indices",
        "anchor_values",
        "adjustment_reason",
    ]
)
BENCHMARK_SUMMARY_COLUMNS = ",".join(
    [
        "id",
        "equipment_id",
        "channel_id",
        "name",
        "normalized_points",
        "source_cycle_count",
        "created_at",
        "measurement",
        "field",
        "tag_id",
        "source_unit",
        "benchmark_unit",
        "conversion_applied",
        "file_name",
        "adjusted_from",
        "adjusted_from_file_name",
    ]
)


def _safe_filename_component(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    return text.strip("_") or fallback


def normalize_cycle(cycle_df, n_points: int = 300, value_col: str = "smooth"):
    y = cycle_df[value_col].to_numpy()
    if len(y) < 2:
        raise ValueError("Cycle too short to normalize.")
    x_old = np.linspace(0, 1, len(y))
    x_new = np.linspace(0, 1, n_points)
    return np.interp(x_new, x_old, y)


def build_benchmark(
    df,
    cycles_idx,
    selected_cycle_indices=None,
    n_points: int = 300,
    value_col: str = "smooth",
    benchmark_name: str = "default_benchmark",
):
    if len(cycles_idx) == 0:
        raise ValueError("No complete cycles detected, cannot build benchmark.")
    if not selected_cycle_indices:
        selected_cycle_indices = list(range(len(cycles_idx)))

    normalized_cycles = []
    source_cycles = []
    for selected_idx in selected_cycle_indices:
        if (
            isinstance(selected_idx, bool)
            or not isinstance(selected_idx, int)
            or selected_idx < 0
            or selected_idx >= len(cycles_idx)
        ):
            raise ValueError(
                "Detected cycles changed or the selection is no longer valid. "
                "Run Detect Cycles again before building the benchmark."
            )
        s, e = cycles_idx[selected_idx]
        cycle_df = df.iloc[s : e + 1].copy().reset_index(drop=True)
        y_norm = normalize_cycle(cycle_df, n_points=n_points, value_col=value_col)
        normalized_cycles.append(y_norm)
        source_cycles.append(
            {
                "cycle_no": selected_idx + 1,
                "start": cycle_df["time"].iloc[0].isoformat(),
                "end": cycle_df["time"].iloc[-1].isoformat(),
                "num_points_original": int(len(cycle_df)),
                "duration_seconds": float(
                    (cycle_df["time"].iloc[-1] - cycle_df["time"].iloc[0]).total_seconds()
                ),
                "max_value": float(cycle_df[value_col].max()),
                "min_value": float(cycle_df[value_col].min()),
            }
        )

    normalized_array = np.array(normalized_cycles)
    source_unit = (
        df["source_unit"].dropna().iloc[0]
        if df["source_unit"].notna().any()
        else None
    )
    tag_id = df["id"].dropna().iloc[0] if df["id"].notna().any() else None
    benchmark = {
        "benchmark_name": benchmark_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "measurement": (
            df["measurement"].dropna().iloc[0]
            if df["measurement"].notna().any()
            else None
        ),
        "field": df["field"].dropna().iloc[0] if df["field"].notna().any() else None,
        "id": tag_id,
        "tag_id": tag_id,
        "sterilizer_id": tag_id,
        "source_unit": source_unit,
        "benchmark_unit": BENCHMARK_UNIT,
        "conversion_applied": bool(source_unit != BENCHMARK_UNIT),
        "source_cycle_count": int(len(source_cycles)),
        "normalized_points": int(n_points),
        "value_column_used": value_col,
        "benchmark_curve": np.mean(normalized_array, axis=0).tolist(),
        "benchmark_std": np.std(normalized_array, axis=0).tolist(),
        "source_cycles": source_cycles,
    }
    return benchmark, normalized_array.tolist()


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _get_tag_id(data: Dict[str, Any]) -> str:
    return _clean(
        data.get("tag_id")
        or data.get("sterilizer_id")
        or data.get("id")
        or data.get("tagId")
        or data.get("sterilizerId")
    )


def _get_field(data: Dict[str, Any]) -> str:
    return _clean(
        data.get("field")
        or data.get("channel")
        or data.get("field_name")
        or data.get("source_field")
    )


def benchmark_audit_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep provenance in audit history without copying full curves."""
    return {
        "database_id": data.get("database_id"),
        "file_name": data.get("file_name"),
        "benchmark_name": data.get("benchmark_name"),
        "tag_id": _get_tag_id(data),
        "field": _get_field(data),
        "measurement": data.get("measurement"),
        "benchmark_unit": data.get("benchmark_unit") or data.get("unit"),
        "source_unit": data.get("source_unit"),
        "normalized_points": data.get("normalized_points"),
        "source_cycle_count": data.get("source_cycle_count"),
        "created_at": data.get("created_at"),
        "adjusted_from_file_name": data.get("adjusted_from_file_name"),
    }


def validate_benchmark_document(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Benchmark must contain a JSON object.")
    curve = data.get("benchmark_curve")
    if not isinstance(curve, list) or len(curve) < 2:
        raise ValueError("Benchmark curve must contain at least two points.")
    for index, value in enumerate(curve):
        if isinstance(value, bool):
            raise ValueError(f"Benchmark curve point {index + 1} is not a valid number.")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Benchmark curve point {index + 1} is not a valid number."
            ) from exc
        if not math.isfinite(numeric):
            raise ValueError(f"Benchmark curve point {index + 1} must be a finite number.")

    normalized_points = data.get("normalized_points")
    if normalized_points is not None:
        try:
            point_count = int(normalized_points)
        except (TypeError, ValueError) as exc:
            raise ValueError("Benchmark normalized point count is invalid.") from exc
        if point_count != len(curve):
            raise ValueError(
                "Benchmark normalized point count does not match the curve length."
            )

    standard_deviation = data.get("benchmark_std")
    if standard_deviation is not None:
        if not isinstance(standard_deviation, list) or len(standard_deviation) != len(curve):
            raise ValueError(
                "Benchmark standard-deviation curve must match the benchmark curve length."
            )
        for index, value in enumerate(standard_deviation):
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Benchmark standard deviation point {index + 1} is invalid."
                ) from exc
            if not math.isfinite(numeric) or numeric < 0:
                raise ValueError(
                    f"Benchmark standard deviation point {index + 1} is invalid."
                )
    return data


def validate_benchmark_compatibility(
    benchmark: Dict[str, Any],
    *,
    tag_id: str,
    field: str,
    file_name: Optional[str] = None,
) -> None:
    validate_benchmark_document(benchmark)
    expected_tag = _clean(tag_id)
    expected_field = _clean(field).lower()
    benchmark_tag = _get_tag_id(benchmark)
    benchmark_field = _get_field(benchmark).lower()
    label = (
        file_name
        or benchmark.get("file_name")
        or benchmark.get("benchmark_name")
        or "Selected benchmark"
    )
    if not benchmark_tag:
        raise ValueError(
            f"Benchmark {label} has no Plant Data ID metadata. Recreate or repair this benchmark before comparison."
        )
    if benchmark_tag != expected_tag:
        raise ValueError(f"Benchmark {label} belongs to {benchmark_tag}, not {expected_tag}.")
    if not benchmark_field:
        raise ValueError(
            f"Benchmark {label} has no sterilizer field metadata. Recreate or repair this benchmark before comparison."
        )
    if benchmark_field != expected_field:
        raise ValueError(f"Benchmark {label} belongs to {benchmark_field}, not {expected_field}.")

    stored_unit = benchmark.get("benchmark_unit") or benchmark.get("unit") or BENCHMARK_UNIT
    try:
        normalized_stored_unit = normalize_unit_name(str(stored_unit))
    except ValueError as exc:
        raise ValueError(
            f"Benchmark {label} contains an unsupported calculation unit."
        ) from exc
    if normalized_stored_unit != BENCHMARK_UNIT:
        raise ValueError(
            f"Benchmark {label} uses {normalized_stored_unit}, but comparison calculations require {BENCHMARK_UNIT}."
        )


def _row_to_benchmark(row: Dict[str, Any]) -> Dict[str, Any]:
    tag_id = _clean(row.get("tag_id"))
    return {
        "benchmark_id": row.get("id"),
        "database_id": row.get("id"),
        "equipment_id": row.get("equipment_id"),
        "file_name": row.get("file_name"),
        "benchmark_name": row.get("name"),
        "created_at": row.get("created_at"),
        "measurement": row.get("measurement"),
        "field": row.get("field") or row.get("channel_id"),
        # Preserve the former API contract: id identifies the plant signal.
        "id": tag_id,
        "tag_id": tag_id,
        "sterilizer_id": tag_id,
        "source_unit": row.get("source_unit"),
        "unit": row.get("benchmark_unit"),
        "benchmark_unit": row.get("benchmark_unit"),
        "conversion_applied": bool(row.get("conversion_applied")),
        "source_cycle_count": row.get("source_cycle_count"),
        "normalized_points": row.get("normalized_points"),
        "value_column_used": row.get("value_column_used"),
        "benchmark_curve": row.get("curve") or [],
        "benchmark_std": row.get("std") or [],
        "source_cycles": row.get("source_cycles") or [],
        "start_time": row.get("start_time"),
        "stop_time": row.get("stop_time"),
        "adjusted_from": row.get("adjusted_from"),
        "adjusted_from_file_name": row.get("adjusted_from_file_name"),
        "anchor_indices": row.get("anchor_indices") or [],
        "anchor_values": row.get("anchor_values") or [],
        "adjustment_reason": row.get("adjustment_reason"),
    }


def _benchmark_to_list_item(data: Dict[str, Any]) -> Dict[str, Any]:
    tag_id = _get_tag_id(data)
    return {
        "benchmark_id": data.get("database_id"),
        "database_id": data.get("database_id"),
        "file_name": data.get("file_name"),
        "benchmark_name": data.get("benchmark_name"),
        "id": tag_id,
        "tag_id": tag_id,
        "sterilizer_id": tag_id,
        "field": data.get("field"),
        "measurement": data.get("measurement"),
        "unit": data.get("benchmark_unit") or data.get("source_unit"),
        "source_unit": data.get("source_unit") or BENCHMARK_UNIT,
        "benchmark_unit": data.get("benchmark_unit") or BENCHMARK_UNIT,
        "source_cycle_count": data.get("source_cycle_count"),
        "normalized_points": data.get("normalized_points"),
        "created_at": data.get("created_at"),
        "adjusted_from": data.get("adjusted_from"),
        "adjusted_from_file_name": data.get("adjusted_from_file_name"),
        "validation_status": "valid",
        "validation_error": None,
    }


def _canonical_equipment_id(tag_id: str, field: str) -> str:
    canonical_measurement = measurement_for_unit(BENCHMARK_UNIT)
    candidates = get_supabase_client().select(
        EQUIPMENT_TABLE,
        columns="id,name,device_id,influxdb_measurement,influxdb_fields,unit",
        filters={
            "device_id": f"eq.{tag_id}",
            "influxdb_measurement": f"eq.{canonical_measurement}",
        },
    )
    matches = [
        row for row in candidates if field in list(row.get("influxdb_fields") or [])
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one canonical {canonical_measurement} equipment mapping for "
            f"{tag_id} {field}, found {len(matches)}."
        )
    return str(matches[0]["id"])


def _parse_timestamp(value: Any) -> datetime:
    text = _clean(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _source_bounds(source_cycles: List[Dict[str, Any]]) -> Tuple[str, str]:
    starts = [_parse_timestamp(item.get("start")) for item in source_cycles if item.get("start")]
    stops = [_parse_timestamp(item.get("end")) for item in source_cycles if item.get("end")]
    if not starts or not stops:
        raise ValueError("Benchmark source cycles must contain start and end timestamps.")
    return min(starts).isoformat(), max(stops).isoformat()


def _benchmark_file_name(benchmark: Dict[str, Any]) -> str:
    requested = _clean(benchmark.get("file_name"))
    if requested:
        safe_name = os.path.basename(requested)
        if safe_name != requested or not safe_name.lower().endswith(".json"):
            raise ValueError("Invalid benchmark file name.")
        return safe_name
    benchmark_name = _safe_filename_component(
        benchmark.get("benchmark_name"), "default_benchmark"
    )
    tag_id = _safe_filename_component(_get_tag_id(benchmark), "unknown_id")
    field = _safe_filename_component(_get_field(benchmark), "unknown_field")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"{benchmark_name}_{tag_id}_{field}_{timestamp}.json"


def _benchmark_insert_payload(benchmark: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(benchmark)
    tag_id = _get_tag_id(data)
    field = _get_field(data)
    if not tag_id or not field:
        raise ValueError("Benchmark requires a Plant Data ID and sterilizer field.")

    curve = list(data.get("benchmark_curve") or [])
    if data.get("benchmark_std") is None:
        data["benchmark_std"] = [0.0] * len(curve)
    data["normalized_points"] = int(data.get("normalized_points") or len(curve))
    data["source_cycle_count"] = int(
        data.get("source_cycle_count") or len(data.get("source_cycles") or [])
    )
    data["benchmark_unit"] = data.get("benchmark_unit") or BENCHMARK_UNIT
    data["source_unit"] = data.get("source_unit") or data["benchmark_unit"]
    data["created_at"] = data.get("created_at") or datetime.now(timezone.utc).isoformat()
    data["file_name"] = _benchmark_file_name(data)
    validate_benchmark_document(data)
    start_time, stop_time = _source_bounds(list(data.get("source_cycles") or []))

    return {
        "equipment_id": _canonical_equipment_id(tag_id, field),
        "channel_id": field,
        "name": _clean(data.get("benchmark_name")) or "default_benchmark",
        "start_time": start_time,
        "stop_time": stop_time,
        "curve": curve,
        "std": list(data.get("benchmark_std") or []),
        "normalized_points": data["normalized_points"],
        "source_cycle_count": data["source_cycle_count"],
        "created_at": data["created_at"],
        "measurement": data.get("measurement"),
        "field": field,
        "tag_id": tag_id,
        "source_unit": data["source_unit"],
        "benchmark_unit": data["benchmark_unit"],
        "conversion_applied": bool(data.get("conversion_applied")),
        "value_column_used": data.get("value_column_used") or "smooth",
        "source_cycles": list(data.get("source_cycles") or []),
        "file_name": data["file_name"],
        "adjusted_from": data.get("adjusted_from"),
        "adjusted_from_file_name": data.get("adjusted_from_file_name"),
        "anchor_indices": list(data.get("anchor_indices") or []),
        "anchor_values": list(data.get("anchor_values") or []),
        "adjustment_reason": data.get("adjustment_reason"),
    }


def save_benchmark_json(benchmark: Dict[str, Any], folder: Optional[str] = None) -> str:
    """Persist a benchmark in Supabase while keeping the former function name."""
    del folder
    payload = _benchmark_insert_payload(benchmark)
    client = get_supabase_client()
    existing = client.select(
        BENCHMARK_TABLE,
        columns="id,file_name",
        filters={"file_name": f"eq.{payload['file_name']}"},
        limit=1,
    )
    if existing:
        raise FileExistsError(f"Benchmark already exists: {payload['file_name']}")

    inserted = client.insert(BENCHMARK_TABLE, payload)
    if len(inserted) != 1:
        raise RuntimeError("Supabase did not return the inserted benchmark.")
    stored = _row_to_benchmark(inserted[0])
    validate_benchmark_document(stored)
    benchmark.update(stored)
    audit_result = try_record_audit_event(
        action="benchmark_created",
        entity_type="benchmark",
        entity_id=str(stored.get("database_id") or stored.get("file_name")),
        after=benchmark_audit_summary(stored),
    )
    benchmark["audit_status"] = audit_result["status"]
    return str(stored["file_name"])


def list_benchmarks(folder: Optional[str] = None) -> List[Dict[str, Any]]:
    del folder
    rows = get_supabase_client().select(
        BENCHMARK_TABLE,
        columns=BENCHMARK_SUMMARY_COLUMNS,
        order="created_at.desc",
    )
    valid_items: List[Dict[str, Any]] = []
    invalid_items: List[Dict[str, Any]] = []
    for row in rows:
        try:
            data = _row_to_benchmark(row)
            if not data.get("database_id"):
                raise ValueError("Benchmark database ID is missing.")
            if not data.get("file_name"):
                raise ValueError("Benchmark filename is missing.")
            if not _get_tag_id(data) or not _get_field(data):
                raise ValueError("Benchmark plant or sterilizer metadata is missing.")
            valid_items.append(_benchmark_to_list_item(data))
        except Exception as exc:
            invalid_items.append(
                {
                    "benchmark_id": row.get("id"),
                    "database_id": row.get("id"),
                    "file_name": row.get("file_name") or "unknown.json",
                    "benchmark_name": row.get("name"),
                    "source_unit": row.get("source_unit") or BENCHMARK_UNIT,
                    "validation_status": "invalid",
                    "validation_error": str(exc),
                }
            )
    return valid_items + invalid_items


def load_benchmark(identifier: str, folder: Optional[str] = None) -> Dict[str, Any]:
    """Load full benchmark detail by UUID, with filename compatibility."""

    del folder
    clean_identifier = str(identifier or "").strip()
    try:
        benchmark_uuid = str(UUID(clean_identifier))
    except (ValueError, TypeError, AttributeError):
        benchmark_uuid = ""

    if benchmark_uuid:
        filters = {"id": f"eq.{benchmark_uuid}"}
        missing_label = benchmark_uuid
    else:
        safe_name = os.path.basename(clean_identifier)
        if safe_name != clean_identifier or not safe_name.lower().endswith(".json"):
            raise ValueError("Invalid benchmark ID or file name.")
        filters = {"file_name": f"eq.{safe_name}"}
        missing_label = safe_name

    rows = get_supabase_client().select(
        BENCHMARK_TABLE,
        columns=BENCHMARK_SELECT_COLUMNS,
        filters=filters,
        limit=2,
    )
    if not rows:
        raise FileNotFoundError(f"Benchmark not found: {missing_label}")
    if len(rows) > 1:
        raise RuntimeError(f"Duplicate benchmark identifier in Supabase: {missing_label}")
    data = _row_to_benchmark(rows[0])
    validate_benchmark_document(data)
    return data
