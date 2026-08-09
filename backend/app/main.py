from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.models.schemas import (
    DetectCyclesRequest,
    DetectCyclesResponse,
    BuildBenchmarkRequest,
    BenchmarkResponse,
    BenchmarkListResponse,
    BenchmarkLoadResponse,
    CompareCyclesRequest,
    CompareCyclesResponse,
    LiveMonitorRequest,
)

from app.services.influx_service import fetch_data, fetch_multi_field_data
from app.services.signal_service import prepare_signal
from app.services.cycle_service import (
    detect_full_cycles,
    refine_cycle_boundaries,
    build_cycle_summary,
)
from app.services.benchmark_service import (
    build_benchmark,
    save_benchmark_json,
    list_benchmarks,
    load_benchmark,
)
from app.services.comparison_service import (
    compare_detected_cycles_to_benchmark,
    build_continuous_window_overlay,
)
from app.services.sterilizer_mapping import get_all_tags, get_sterilizers_for_tag
from app.services.live_monitor_service import (
    get_live_window_iso,
    build_live_sterilizer_payload,
)

from pathlib import Path
from datetime import datetime
import json
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv
load_dotenv()

from pydantic import BaseModel

# NOTE: The optional AI chatbot / semantic-retrieval routers (rag_routes,
# chatbot_routes) are intentionally NOT imported here. They pull in
# torch / transformers / sentence-transformers / qdrant-client, which are
# heavy (multi-GB) and unnecessary for the core dashboard (live monitoring,
# benchmarking, comparison, and rule-based RCA evaluation). All rule
# management functions below degrade gracefully (try/except) if Qdrant/
# embeddings are unavailable, so nothing here breaks without them.
from app.rag.custom_rule_service import (
    list_rules as list_rca_rules,
    create_custom_rule,
    update_custom_rule,
    delete_custom_rule,
    reindex_all_custom_rules_to_qdrant,
    reindex_all_rules_to_qdrant,
    verify_rule_installation,
    get_all_chunks_for_indexing,
    RuleConflictWarning,
)
from app.rag.rule_evaluator import evaluate_rules

app = FastAPI(title="Sterilizer Benchmark API")

# ---------------------------------------------------------------------
# Optional LLM preload
# ---------------------------------------------------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@app.on_event("startup")
def preload_local_llm():
    """
    Preload and optionally warm up the local LLM when FastAPI starts.
    This reduces the first RCA generation delay.
    """
    if not _env_bool("ENABLE_LLM_GENERATION", False):
        print("[LLM] ENABLE_LLM_GENERATION=false, skip preload.")
        return

    if not _env_bool("LLM_PRELOAD_ON_STARTUP", False):
        print("[LLM] LLM_PRELOAD_ON_STARTUP=false, skip preload.")
        return

    try:
        from app.rag.llm_service import load_llm, generate_llm_text

        print("[LLM] Preloading local LLM...")
        load_llm()
        print("[LLM] Local LLM preloaded successfully.")

        if _env_bool("LLM_WARMUP_ON_STARTUP", True):
            print("[LLM] Warming up local LLM generation...")
            warmup_text = generate_llm_text("Reply with one word only: ready")
            print(f"[LLM] Warmup result: {warmup_text}")

    except Exception as e:
        print(f"[LLM] Failed to preload/warm up local LLM: {e}")

# ---------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------
allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

production_origin = os.getenv("FRONTEND_ORIGIN")
if production_origin:
    allowed_origins.append(production_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------
def validate_common_inputs(payload):
    if not payload.bucket or not payload.bucket.strip():
        raise HTTPException(status_code=400, detail="Bucket is required.")
    if not payload.measurement or not payload.measurement.strip():
        raise HTTPException(status_code=400, detail="Measurement is required.")
    if not payload.field or not payload.field.strip():
        raise HTTPException(status_code=400, detail="Field / Channel is required.")
    if not payload.tag_id or not payload.tag_id.strip():
        raise HTTPException(status_code=400, detail="Tag ID is required.")
    if not payload.start_time or not str(payload.start_time).strip():
        raise HTTPException(status_code=400, detail="Start Time is required.")
    if not payload.stop_time or not str(payload.stop_time).strip():
        raise HTTPException(status_code=400, detail="Stop Time is required.")
    if not payload.source_unit or not payload.source_unit.strip():
        raise HTTPException(status_code=400, detail="Source Unit is required.")
    if payload.smooth_window < 1:
        raise HTTPException(status_code=400, detail="Smooth Window must be greater than 0.")


def get_signal_value_col(df) -> str:
    """
    Select the correct pressure column for signal processing.

    Some dataframes already contain value_std.
    Some only contain value.
    If the dataframe is empty or does not contain usable pressure values,
    return a user-friendly no-data error instead of KeyError: 'value_std'.
    """
    if df is None or df.empty:
        raise ValueError("No data found for the selected time range and Tag ID.")

    if "value_std" in df.columns and df["value_std"].notna().any():
        return "value_std"

    if "value" in df.columns and df["value"].notna().any():
        return "value"

    raise ValueError("No usable pressure values found for the selected time range and Tag ID.")


def prepare_df_for_analysis(df, smooth_window: int):
    """
    Prepare signal using value_std when available, otherwise fall back to value.
    """
    value_col = get_signal_value_col(df)

    return prepare_signal(
        df,
        smooth_window=smooth_window,
        value_col=value_col,
    )


def friendly_error_message(error: Exception) -> str:
    message = str(error)
    lower_msg = message.lower()

    # No-data related errors should be shown as no-data, not value_std/internal backend errors.
    if (
        "no data returned from influxdb" in lower_msg
        or "no data found for the selected time range" in lower_msg
        or "no usable pressure values" in lower_msg
        or "empty dataframe" in lower_msg
        or "index 0 is out of bounds" in lower_msg
        or "single positional indexer is out-of-bounds" in lower_msg
    ):
        return "No data found for the selected time range and Tag ID."

    if "cannot parse date" in lower_msg or "invalid date" in lower_msg:
        return "Invalid date or time format."

    if "no unit found in data" in lower_msg:
        return "No unit found in the database. Please choose the correct Source Unit."

    if "mixed units found" in lower_msg:
        return "Multiple units were found in the selected data. Please check the selected source."

    if "benchmark file name is required" in lower_msg:
        return "Please choose a benchmark before running comparison."

    if "benchmark file not found" in lower_msg:
        return "Selected benchmark file could not be found."

    if "no cycles detected in the selected real-time range" in lower_msg:
        return "No complete cycle was detected in the selected real-time range."

    if "cycle too short" in lower_msg:
        return "Detected cycle is too short for comparison."

    # This used to return a misleading message.
    # In most UI cases, KeyError: 'value_std' happens because there is no usable data
    # or the dataframe returned only the raw value column.
    if "value_std" in lower_msg:
        return (
            "No usable standardised pressure data was found for the selected range. "
            "Please check whether the selected Tag ID, field/channel, unit, and time range contain data."
        )

    if "unexpected keyword argument" in lower_msg and "anomaly_threshold" in lower_msg:
        return (
            "Comparison service function signature does not match main.py. "
            "Please remove anomaly_threshold from compare_detected_cycles_to_benchmark call "
            "or update comparison_service.py to accept it."
        )

    return message


# ---------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------
@app.get("/")
def root():
    return {"message": "Sterilizer Benchmark API is running"}


# ---------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------
@app.post("/api/detect-cycles", response_model=DetectCyclesResponse)
def detect_cycles(payload: DetectCyclesRequest):
    validate_common_inputs(payload)

    try:
        df = fetch_data(
            bucket=payload.bucket,
            measurement=payload.measurement,
            field=payload.field,
            tag_id=payload.tag_id,
            start_time=payload.start_time,
            stop_time=payload.stop_time,
            source_unit_fallback=payload.source_unit,
        )

        df, baseline_value, baseline_margin = prepare_df_for_analysis(
            df,
            smooth_window=payload.smooth_window,
        )

        df, cycles_idx, threshold = detect_full_cycles(
            df,
            baseline_value,
            baseline_margin,
        )

        cycles_idx = refine_cycle_boundaries(
            df,
            cycles_idx,
            baseline_value,
            baseline_margin,
            search_points=40,
            stable_points=5,
        )

        cycles = build_cycle_summary(df, cycles_idx, value_col="smooth")

        source_unit = (
            df["source_unit"].dropna().iloc[0]
            if "source_unit" in df.columns and df["source_unit"].notna().any()
            else None
        )

        benchmark_unit = (
            df["benchmark_unit"].dropna().iloc[0]
            if "benchmark_unit" in df.columns and df["benchmark_unit"].notna().any()
            else None
        )

        return {
            "times": [t.isoformat() for t in df["time"]],
            "values": df["value"].astype(float).tolist(),
            "smooth": df["smooth"].astype(float).tolist(),
            "baseline_value": float(baseline_value),
            "baseline_margin": float(baseline_margin),
            "threshold": float(threshold),
            "cycles": cycles,
            "source_unit": source_unit,
            "benchmark_unit": benchmark_unit,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


# ---------------------------------------------------------------------
# Benchmark generation
# ---------------------------------------------------------------------
@app.post("/api/build-benchmark", response_model=BenchmarkResponse)
def create_benchmark(payload: BuildBenchmarkRequest):
    validate_common_inputs(payload)

    if not payload.benchmark_name or not payload.benchmark_name.strip():
        raise HTTPException(status_code=400, detail="Benchmark Name is required.")

    if payload.normalized_points < 10:
        raise HTTPException(status_code=400, detail="Normalized Points must be at least 10.")

    try:
        df = fetch_data(
            bucket=payload.bucket,
            measurement=payload.measurement,
            field=payload.field,
            tag_id=payload.tag_id,
            start_time=payload.start_time,
            stop_time=payload.stop_time,
            source_unit_fallback=payload.source_unit,
        )

        df, baseline_value, baseline_margin = prepare_df_for_analysis(
            df,
            smooth_window=payload.smooth_window,
        )

        df, cycles_idx, threshold = detect_full_cycles(
            df,
            baseline_value,
            baseline_margin,
        )

        cycles_idx = refine_cycle_boundaries(
            df,
            cycles_idx,
            baseline_value,
            baseline_margin,
            search_points=40,
            stable_points=5,
        )

        benchmark, normalized_cycles = build_benchmark(
            df=df,
            cycles_idx=cycles_idx,
            selected_cycle_indices=payload.selected_cycle_indices,
            n_points=payload.normalized_points,
            value_col="smooth",
            benchmark_name=payload.benchmark_name,
        )

        save_path = save_benchmark_json(benchmark)
        benchmark["saved_path"] = save_path

        return {
            "benchmark": benchmark,
            "normalized_cycles": normalized_cycles,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.get("/api/benchmarks", response_model=BenchmarkListResponse)
def get_benchmarks():
    try:
        items = list_benchmarks()
        return {"benchmarks": items}
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.get("/api/benchmarks/{file_name}", response_model=BenchmarkLoadResponse)
def get_one_benchmark(file_name: str):
    try:
        benchmark = load_benchmark(file_name)
        return {"benchmark": benchmark}
    except Exception as e:
        raise HTTPException(status_code=404, detail=friendly_error_message(e))


# ---------------------------------------------------------------------
# AI Comparison / V4 comparison
# ---------------------------------------------------------------------
@app.post("/api/compare-cycles", response_model=CompareCyclesResponse)
def compare_cycles(payload: CompareCyclesRequest):
    validate_common_inputs(payload)

    if not payload.benchmark_file_name or not payload.benchmark_file_name.strip():
        raise HTTPException(status_code=400, detail="Benchmark file name is required.")

    try:
        benchmark = load_benchmark(payload.benchmark_file_name)

        df = fetch_data(
            bucket=payload.bucket,
            measurement=payload.measurement,
            field=payload.field,
            tag_id=payload.tag_id,
            start_time=payload.start_time,
            stop_time=payload.stop_time,
            source_unit_fallback=payload.source_unit,
        )

        df, baseline_value, baseline_margin = prepare_df_for_analysis(
            df,
            smooth_window=payload.smooth_window,
        )

        df, cycles_idx, threshold = detect_full_cycles(
            df,
            baseline_value,
            baseline_margin,
        )

        cycles_idx = refine_cycle_boundaries(
            df,
            cycles_idx,
            baseline_value,
            baseline_margin,
            search_points=40,
            stable_points=5,
        )

        if len(cycles_idx) == 0:
            raise ValueError("No cycles detected in the selected real-time range.")

        cycle_results = compare_detected_cycles_to_benchmark(
            df=df,
            cycles_idx=cycles_idx,
            benchmark=benchmark,
            value_col="smooth",
        )

        continuous_overlay_chart = build_continuous_window_overlay(
            df=df,
            cycles_idx=cycles_idx,
            cycle_results=cycle_results,
            benchmark=benchmark,
        )

        return {
            "benchmark_name": benchmark.get("benchmark_name"),
            "benchmark_id": benchmark.get("id") or benchmark.get("sterilizer_id"),
            "benchmark_field": benchmark.get("field"),
            "benchmark_unit": benchmark.get("benchmark_unit") or benchmark.get("unit"),
            "cycle_results": cycle_results,
            "continuous_overlay_chart": continuous_overlay_chart,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))

# ---------------------------------------------------------------------
# Save adjusted benchmark
# ---------------------------------------------------------------------
BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "benchmarks"
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)


class SaveAdjustedBenchmarkRequest(BaseModel):
    benchmark_name: str
    benchmark_curve: list[float]
    normalized_points: int | None = None
    unit: str | None = None
    measurement: str | None = None
    field: str | None = None
    sterilizer_id: str | None = None
    source_cycle_count: int | None = None
    source_cycles: list[dict] | None = None
    created_at: str | None = None
    adjusted_from: str | None = None
    anchor_indices: list[int] | None = None
    anchor_values: list[float] | None = None


def _safe_filename(name: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in ("_", "-") else "_"
        for ch in name.strip()
    )
    return cleaned or "adjusted_benchmark"


@app.post("/api/benchmarks/save-adjusted")
def save_adjusted_benchmark(payload: SaveAdjustedBenchmarkRequest) -> Dict[str, Any]:
    try:
        if not payload.benchmark_curve:
            raise HTTPException(status_code=400, detail="Benchmark curve is empty.")

        benchmark_name = payload.benchmark_name.strip()
        if not benchmark_name:
            raise HTTPException(status_code=400, detail="Benchmark name is required.")

        file_name = f"{_safe_filename(benchmark_name)}.json"
        file_path = BENCHMARK_DIR / file_name

        unit = payload.unit or "bar"

        benchmark_data = {
            "benchmark_name": benchmark_name,
            "benchmark_curve": payload.benchmark_curve,
            "normalized_points": payload.normalized_points or len(payload.benchmark_curve),
            "unit": unit,
            "benchmark_unit": unit,
            "source_unit": unit,
            "measurement": payload.measurement,
            "field": payload.field,
            "id": payload.sterilizer_id,
            "sterilizer_id": payload.sterilizer_id,
            "source_cycle_count": payload.source_cycle_count,
            "source_cycles": payload.source_cycles or [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "adjusted_from": payload.adjusted_from,
            "anchor_indices": payload.anchor_indices or [],
            "anchor_values": payload.anchor_values or [],
            "value_column_used": "manual_adjusted_curve",
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(benchmark_data, f, indent=2)

        benchmark_data["file_name"] = file_name

        return {
            "message": "Adjusted benchmark saved successfully.",
            "file_name": file_name,
            "benchmark": benchmark_data,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


# ---------------------------------------------------------------------
# Tag / sterilizer mapping
# ---------------------------------------------------------------------
@app.get("/api/tags")
def list_tags():
    return {"tags": get_all_tags()}


@app.get("/api/tag-sterilizers/{tag_id}")
def get_tag_sterilizers(tag_id: str):
    sterilizers = get_sterilizers_for_tag(tag_id)

    if not sterilizers:
        raise HTTPException(status_code=404, detail="No sterilizers found for selected tag.")

    return {"tag_id": tag_id, "sterilizers": sterilizers}


# ---------------------------------------------------------------------
# Live monitoring
# ---------------------------------------------------------------------
def get_live_aggregate_window(hours: int) -> str:
    if hours <= 3:
        return "30s"
    if hours <= 6:
        return "1m"
    if hours <= 12:
        return "2m"
    return "5m"


@app.post("/api/live-monitor")
def live_monitor(payload: LiveMonitorRequest):
    api_started = time.perf_counter()

    try:
        sterilizers = get_sterilizers_for_tag(payload.tag_id)

        if not sterilizers:
            raise ValueError("No sterilizers found for selected tag.")

        selected_hours = int(payload.last_n_hours or 3)

        if selected_hours not in [3, 6, 12, 24]:
            raise ValueError("Live monitoring time range must be 3, 6, 12, or 24 hours.")

        aggregate_every = get_live_aggregate_window(selected_hours)

        start_time, stop_time = get_live_window_iso(
            start_time=payload.start_time,
            stop_time=payload.stop_time,
            last_n_hours=selected_hours,
        )

        fields = [item["field"] for item in sterilizers]
        field_to_name = {
            item["field"]: item["sterilizer_name"]
            for item in sterilizers
        }

        query_started = time.perf_counter()

        all_df = fetch_multi_field_data(
            bucket=payload.bucket,
            measurement=payload.measurement,
            fields=fields,
            tag_id=payload.tag_id,
            start_time=start_time,
            stop_time=stop_time,
            source_unit_fallback=payload.source_unit,
            aggregate_every=aggregate_every,
        )

        query_seconds = time.perf_counter() - query_started

        live_results = []
        processing_started = time.perf_counter()

        for field in fields:
            sterilizer_name = field_to_name[field]

            df = (
                all_df[all_df["field"] == field].copy().reset_index(drop=True)
                if not all_df.empty and "field" in all_df.columns
                else all_df
            )

            try:
                if df.empty:
                    sterilizer_result = {
                        "sterilizer_name": sterilizer_name,
                        "field": field,
                        "latest_value": None,
                        "status": "no_data",
                        "points": [],
                        "cycles": [],
                        "error": f"No data found in the latest {selected_hours} hours.",
                    }
                else:
                    df, baseline_value, baseline_margin = prepare_df_for_analysis(
                        df,
                        smooth_window=payload.smooth_window,
                    )

                    df, cycles_idx, threshold = detect_full_cycles(
                        df,
                        baseline_value,
                        baseline_margin,
                    )

                    cycles_idx = refine_cycle_boundaries(
                        df,
                        cycles_idx,
                        baseline_value,
                        baseline_margin,
                        search_points=40,
                        stable_points=5,
                    )

                    sterilizer_result = build_live_sterilizer_payload(
                        df=df,
                        cycles_idx=cycles_idx,
                        sterilizer_name=sterilizer_name,
                        field=field,
                        display_unit=payload.source_unit,
                    )

            except Exception as e:
                sterilizer_result = {
                    "sterilizer_name": sterilizer_name,
                    "field": field,
                    "latest_value": None,
                    "status": "no_data",
                    "points": [],
                    "cycles": [],
                    "error": friendly_error_message(e),
                }

            live_results.append(sterilizer_result)

        processing_seconds = time.perf_counter() - processing_started
        total_seconds = time.perf_counter() - api_started

        active_cycle_count = sum(
            1 for item in live_results
            if item["status"] == "active"
        )

        return {
            "tag_id": payload.tag_id,
            "tag_name": payload.tag_id,
            "start_time": start_time,
            "stop_time": stop_time,
            "updated_at": stop_time,
            "window_hours_used": selected_hours,
            "window_minutes_used": selected_hours * 60,
            "active_cycle_count": active_cycle_count,
            "sterilizer_count": len(live_results),
            "sterilizers": live_results,
            "aggregate_every": aggregate_every,
            "performance": {
                "query_seconds": round(query_seconds, 3),
                "processing_seconds": round(processing_seconds, 3),
                "total_seconds": round(total_seconds, 3),
                "query_mode": "multi_field_single_query",
            },
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))

# ---------------------------------------------------------------------
# RCA Rule Evaluation (rule-based only, no semantic retrieval required)
# ---------------------------------------------------------------------
class EvaluateRcaRulesRequest(BaseModel):
    metrics: Dict[str, Any]
    stage: Optional[str] = None
    peer_confirmation_available: bool = False
    evidence: Optional[Dict[str, Any]] = None
    include_original: bool = True


@app.post("/api/evaluate-rca-rules")
def evaluate_rca_rules(payload: EvaluateRcaRulesRequest):
    """
    Evaluate scoring metrics against all active RCA rules directly,
    without going through semantic retrieval. This lets the RCA feedback
    feature work fully deployed without Qdrant/embeddings configured.
    """
    try:
        chunks = get_all_chunks_for_indexing()

        if payload.stage:
            chunks = [c for c in chunks if str(c.get("stage")) == str(payload.stage)]

        results = evaluate_rules(
            chunks=chunks,
            metrics=payload.metrics,
            peer_confirmation_available=payload.peer_confirmation_available,
            evidence=payload.evidence,
        )

        return {
            "rule_count": len(chunks),
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


# ---------------------------------------------------------------------
# RCA Rule Management
# ---------------------------------------------------------------------
class RcaRuleRequest(BaseModel):
    rule_id: str
    stage: str
    priority: str
    attribution: str
    pattern: str
    metric_name: str
    warn_low: float
    critical_value: float
    recommendation_en: str
    rec_key: Optional[str] = None
    recommendation_bm: Optional[str] = ""
    urgency: Optional[str] = "High"
    target_metric: Optional[str] = None
    include_for_anomaly_retrieval: bool = True
    allow_conflict: bool = False


@app.get("/api/rca-rules")
def get_rca_rules(include_original: bool = True):
    """
    List original Excel-derived rules and custom rules.

    Both original and custom rules are editable from the management page.
    Original edits are written to the parsed/chunked knowledge-base JSON.
    Custom rules are stored in backend/knowledge_base/custom_rules/custom_rca_rules.json.
    """
    try:
        return list_rca_rules(include_original=include_original)
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.post("/api/rca-rules")
def create_rca_rule(payload: RcaRuleRequest):
    """
    Create one custom RCA rule and its one-to-one recommendation.
    The rule is saved to JSON, converted to chunk format, and upserted into Qdrant immediately.
    """
    try:
        rule, reindex_result = create_custom_rule(payload.dict())
        verification = verify_rule_installation(rule.get("rule_id"))
        return {
            "message": "Custom RCA rule created successfully.",
            "rule": rule,
            "qdrant_reindex": reindex_result,
            "verification": verification,
        }
    except RuleConflictWarning as conflict:
        raise HTTPException(status_code=409, detail=conflict.to_response())
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.post("/api/rca-rules/reindex-custom")
def reindex_custom_rca_rules():
    """
    Backward-compatible endpoint. Reindexes custom rules only.
    """
    try:
        return reindex_all_custom_rules_to_qdrant()
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.post("/api/rca-rules/reindex-all")
def reindex_all_rca_rules():
    """
    Upsert both edited original rule chunks and custom rule chunks into Qdrant.
    """
    try:
        return reindex_all_rules_to_qdrant()
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.get("/api/rca-rules/{rule_id}/verify")
def verify_rca_rule(rule_id: str):
    """
    Verify that a rule is saved in JSON/chunk JSON, indexed in Qdrant if possible,
    and directly available to RCA retrieval.
    This lets the UI confirm the Save button completed the full pipeline.
    """
    try:
        return verify_rule_installation(rule_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.put("/api/rca-rules/{rule_id}")
def update_rca_rule(rule_id: str, payload: RcaRuleRequest):
    """
    Update an existing RCA rule. Original Excel-derived rules and custom rules are both editable.
    """
    try:
        rule, reindex_result = update_custom_rule(rule_id, payload.dict())
        verification = verify_rule_installation(rule.get("rule_id"))
        return {
            "message": "RCA rule updated successfully.",
            "rule": rule,
            "qdrant_reindex": reindex_result,
            "verification": verification,
        }
    except RuleConflictWarning as conflict:
        raise HTTPException(status_code=409, detail=conflict.to_response())
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))


@app.delete("/api/rca-rules/{rule_id}")
def delete_rca_rule(rule_id: str):
    """
    Delete an existing RCA rule. For original Excel-derived rules, this removes the parsed/chunked JSON rule and its Qdrant point. It does not edit the source Excel file.
    """
    try:
        qdrant_delete = delete_custom_rule(rule_id)
        return {
            "message": "RCA rule deleted successfully.",
            "delete_result": qdrant_delete,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=friendly_error_message(e))

