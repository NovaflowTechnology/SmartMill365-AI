import json
import os
import numpy as np
from datetime import datetime, timezone

from app.config import BENCHMARK_UNIT

BENCHMARK_FOLDER = "benchmarks"


def normalize_cycle(cycle_df, n_points=300, value_col="smooth"):
    y = cycle_df[value_col].to_numpy()
    if len(y) < 2:
        raise ValueError("Cycle too short to normalize.")

    x_old = np.linspace(0, 1, len(y))
    x_new = np.linspace(0, 1, n_points)
    y_new = np.interp(x_new, x_old, y)
    return y_new


def build_benchmark(
    df,
    cycles_idx,
    selected_cycle_indices=None,
    n_points=300,
    value_col="smooth",
    benchmark_name="default_benchmark"
):
    if len(cycles_idx) == 0:
        raise ValueError("No cycles detected, cannot build benchmark.")

    if not selected_cycle_indices:
        selected_cycle_indices = list(range(len(cycles_idx)))

    normalized_cycles = []
    source_cycles = []

    for selected_idx in selected_cycle_indices:
        s, e = cycles_idx[selected_idx]
        cycle_df = df.iloc[s:e + 1].copy().reset_index(drop=True)

        y_norm = normalize_cycle(cycle_df, n_points=n_points, value_col=value_col)
        normalized_cycles.append(y_norm)

        source_cycles.append({
            "cycle_no": selected_idx + 1,
            "start": cycle_df["time"].iloc[0].isoformat(),
            "end": cycle_df["time"].iloc[-1].isoformat(),
            "num_points_original": int(len(cycle_df)),
            "duration_seconds": float(
                (cycle_df["time"].iloc[-1] - cycle_df["time"].iloc[0]).total_seconds()
            ),
            "max_value": float(cycle_df[value_col].max()),
            "min_value": float(cycle_df[value_col].min())
        })

    normalized_cycles = np.array(normalized_cycles)
    benchmark_curve = np.mean(normalized_cycles, axis=0)
    benchmark_std = np.std(normalized_cycles, axis=0)

    source_unit = df["source_unit"].dropna().iloc[0] if df["source_unit"].notna().any() else None
    tag_id = df["id"].dropna().iloc[0] if df["id"].notna().any() else None

    benchmark = {
        "benchmark_name": benchmark_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "measurement": df["measurement"].dropna().iloc[0] if df["measurement"].notna().any() else None,
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
        "benchmark_curve": benchmark_curve.tolist(),
        "benchmark_std": benchmark_std.tolist(),
        "source_cycles": source_cycles
    }

    return benchmark, normalized_cycles.tolist()


def save_benchmark_json(benchmark, folder=BENCHMARK_FOLDER):
    os.makedirs(folder, exist_ok=True)

    benchmark_id = benchmark.get("id") or benchmark.get("sterilizer_id") or benchmark.get("tag_id") or "unknown_id"
    benchmark_field = benchmark.get("field", "unknown_field")
    benchmark_name = benchmark.get("benchmark_name", "default_benchmark").replace(" ", "_")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    filename = f"{benchmark_name}_{benchmark_id}_{benchmark_field}_{timestamp}.json"
    path = os.path.join(folder, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(benchmark, f, indent=2)

    return path


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalise_name(value):
    text = _clean(value)
    if text.lower().endswith(".json"):
        text = text[:-5]
    return text.lower().replace(" ", "_")


def _get_tag_id(data):
    return _clean(
        data.get("tag_id") or
        data.get("sterilizer_id") or
        data.get("id") or
        data.get("tagId") or
        data.get("sterilizerId")
    )


def _get_field(data):
    return _clean(
        data.get("field") or
        data.get("channel") or
        data.get("field_name") or
        data.get("source_field")
    )


def _infer_source_benchmark(data, all_items):
    """
    Older adjusted benchmarks may have field=ch4 but id/tag_id missing.
    This tries to infer the missing tag from the original benchmark.

    Examples:
    - adjusted_from == "Testing_Benchmark" -> use Testing_Benchmark metadata
    - Testing_Benchmark_adjusted2 -> use Testing_Benchmark metadata
    """
    current_field = _get_field(data)
    adjusted_from = _normalise_name(data.get("adjusted_from") or data.get("adjustedFrom"))
    adjusted_from_file = _normalise_name(
        data.get("adjusted_from_file_name") or data.get("adjustedFromFileName")
    )
    current_name = _normalise_name(data.get("benchmark_name"))
    current_file = _normalise_name(data.get("file_name"))

    candidates = []
    for item in all_items:
        if item is data:
            continue
        if not _get_tag_id(item):
            continue

        item_field = _get_field(item)
        if current_field and item_field and current_field != item_field:
            continue

        candidates.append(item)

    if adjusted_from or adjusted_from_file:
        for item in candidates:
            item_name = _normalise_name(item.get("benchmark_name"))
            item_file = _normalise_name(item.get("file_name"))

            if adjusted_from and (item_name == adjusted_from or item_file == adjusted_from):
                return item
            if adjusted_from_file and item_file == adjusted_from_file:
                return item

    prefix_matches = []
    for item in candidates:
        item_name = _normalise_name(item.get("benchmark_name"))
        if not item_name:
            continue

        if (
            current_name.startswith(f"{item_name}_adjusted") or
            current_file.startswith(f"{item_name}_adjusted")
        ):
            prefix_matches.append(item)

    if prefix_matches:
        # Pick the longest name so "Testing_Benchmark" wins over "Testing".
        prefix_matches.sort(key=lambda x: len(_normalise_name(x.get("benchmark_name"))), reverse=True)
        return prefix_matches[0]

    return None


def _normalise_benchmark_item(data, all_items):
    item = dict(data)

    tag_id = _get_tag_id(item)
    field = _get_field(item)

    if not tag_id:
        source = _infer_source_benchmark(item, all_items)
        if source:
            tag_id = _get_tag_id(source)
            if not field:
                field = _get_field(source)
            item["inferred_tag_id"] = tag_id
            item["inferred_from_benchmark"] = source.get("benchmark_name") or source.get("file_name")
            item["display_context_source"] = "inferred_from_original_benchmark"

    if tag_id:
        item["id"] = item.get("id") or tag_id
        item["tag_id"] = item.get("tag_id") or tag_id
        item["sterilizer_id"] = item.get("sterilizer_id") or tag_id

    if field:
        item["field"] = field

    return item


def list_benchmarks(folder=BENCHMARK_FOLDER):
    os.makedirs(folder, exist_ok=True)

    raw_items = []

    for file_name in os.listdir(folder):
        if not file_name.endswith(".json"):
            continue

        full_path = os.path.join(folder, file_name)

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["file_name"] = file_name
            raw_items.append(data)
        except Exception:
            continue

    normalised_items = [_normalise_benchmark_item(item, raw_items) for item in raw_items]

    items = []
    for data in normalised_items:
        items.append({
            "file_name": data.get("file_name"),
            "benchmark_name": data.get("benchmark_name"),
            "id": data.get("id") or data.get("sterilizer_id") or data.get("tag_id"),
            "tag_id": data.get("tag_id") or data.get("id") or data.get("sterilizer_id"),
            "sterilizer_id": data.get("sterilizer_id") or data.get("id") or data.get("tag_id"),
            "field": data.get("field"),
            "measurement": data.get("measurement"),
            "unit": data.get("benchmark_unit") or data.get("unit") or data.get("source_unit"),
            "source_cycle_count": data.get("source_cycle_count"),
            "normalized_points": data.get("normalized_points"),
            "created_at": data.get("created_at"),
            "adjusted_from": data.get("adjusted_from"),
            "adjusted_from_file_name": data.get("adjusted_from_file_name"),
            "inferred_tag_id": data.get("inferred_tag_id"),
            "inferred_from_benchmark": data.get("inferred_from_benchmark"),
            "display_context_source": data.get("display_context_source"),
        })

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def load_benchmark(file_name, folder=BENCHMARK_FOLDER):
    full_path = os.path.join(folder, file_name)

    if not os.path.exists(full_path):
        raise FileNotFoundError(f"Benchmark file not found: {file_name}")

    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Keep loaded benchmark backward-compatible.
    tag_id = _get_tag_id(data)
    if tag_id:
        data["id"] = data.get("id") or tag_id
        data["tag_id"] = data.get("tag_id") or tag_id
        data["sterilizer_id"] = data.get("sterilizer_id") or tag_id

    return data
