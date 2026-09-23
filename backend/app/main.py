from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
    DailyReportSettingsRequest,
    DailyReportGenerateRequest,
)

from app.services.influx_service import (
    fetch_data,
    fetch_multi_field_data,
    get_pressure_source,
    check_influx_connectivity,
)
from app.services.signal_service import prepare_signal
from app.services.cycle_service import (
    detect_full_cycles,
    refine_cycle_boundaries,
    build_cycle_summary,
    restrict_cycles_to_time_range,
)
from app.services.detection_fingerprint_service import (
    build_detection_fingerprint,
    selected_cycle_indices_from_boundaries,
)
from app.services.benchmark_service import (
    build_benchmark,
    save_benchmark_json,
    list_benchmarks,
    load_benchmark,
    benchmark_audit_summary,
    validate_benchmark_compatibility,
    validate_benchmark_document,
)
from app.services.comparison_service import (
    compare_detected_cycles_to_benchmark,
    build_continuous_window_overlay,
)
from app.services.sterilizer_mapping import get_all_tags, get_sterilizers_for_tag
from app.services.unit_service import (
    convert_values,
    measurement_for_unit,
    normalize_unit_name,
    validate_measurement_matches_unit,
)
from app.services.live_monitor_service import (
    get_live_aggregate_window,
    get_live_window_iso,
    get_live_window_hours,
    build_live_sterilizer_payload,
)
from app.services.daily_report_settings_service import (
    DEFAULT_DAILY_REPORT_SETTINGS,
    DEFAULT_SHIFT_SETTINGS,
    derive_site_code,
    get_plant_display_name,
    get_plant_name_details,
    get_site_config,
    load_daily_report_settings,
    load_daily_report_settings_with_status,
    save_daily_report_settings,
    get_site_display_name,
    load_official_plant_names,
    load_plant_custom_aliases,
    get_active_benchmark_file_name,
)
from app.services.daily_report_service import generate_daily_report

from datetime import datetime, timezone
from copy import deepcopy
import logging
import math
import os
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv()

from pydantic import BaseModel

from app.error_handling import (
    LOGGER,
    REQUEST_ID,
    friendly_error_message as public_friendly_error_message,
    raise_http_error,
)
from app.services.audit_service import try_record_audit_event
from app.services.supabase_service import check_supabase_connectivity
from app.services.timezone_service import normalise_query_range
from app.services.timezone_service import pad_query_range
from app.config import CYCLE_QUERY_PADDING_MINUTES

from app.routers.rag_routes import router as rag_router
from app.routers.chatbot_routes import router as chatbot_router
from app.auth.router import router as auth_router
from app.auth.middleware import authentication_guard
from app.auth.database import check_auth_db_connectivity
from app.rag.custom_rule_service import (
    list_rules as list_rca_rules,
    create_custom_rule,
    update_custom_rule,
    delete_custom_rule,
    reindex_all_custom_rules_to_qdrant,
    reindex_all_rules_to_qdrant,
    verify_rule_installation,
    RuleConflictWarning,
)

BENCHMARK_NORMALIZED_POINTS = 300

app = FastAPI(title="Sterilizer Benchmark API")
app.include_router(auth_router)
app.include_router(rag_router)
app.include_router(chatbot_router)

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@app.middleware("http")
async def add_request_reference(request: Request, call_next):
    """Attach a support reference and record one structured request log line."""

    request_id = request.headers.get("X-Request-ID") or uuid4().hex[:12]
    token = REQUEST_ID.set(request_id)
    started = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        LOGGER.exception(
            "request_id=%s method=%s path=%s status=500 duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            (time.perf_counter() - started) * 1000.0,
        )
        raise
    finally:
        if response is not None:
            LOGGER.info(
                "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                request_id,
                request.method,
                request.url.path,
                response.status_code,
                (time.perf_counter() - started) * 1000.0,
            )
        REQUEST_ID.reset(token)

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

# Authentication and role authorization for all protected API/RAG routes.
app.middleware("http")(authentication_guard)


# ---------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------
def validate_common_inputs(payload):
    if not payload.field or not payload.field.strip():
        raise HTTPException(status_code=400, detail="Sterilizer field is required.")
    if not payload.tag_id or not payload.tag_id.strip():
        raise HTTPException(status_code=400, detail="Data ID is required.")
    if not payload.start_time or not str(payload.start_time).strip():
        raise HTTPException(status_code=400, detail="Start Time is required.")
    if not payload.stop_time or not str(payload.stop_time).strip():
        raise HTTPException(status_code=400, detail="Stop Time is required.")
    if not payload.source_unit or not payload.source_unit.strip():
        raise HTTPException(status_code=400, detail="Pressure Unit is required.")
    try:
        normalize_unit_name(payload.source_unit)
        validate_measurement_matches_unit(
            getattr(payload, "measurement", None), payload.source_unit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.smooth_window < 1:
        raise HTTPException(status_code=400, detail="Smooth Window must be greater than 0.")


def resolve_payload_pressure_source(payload):
    return get_pressure_source(
        source_unit=payload.source_unit,
        requested_bucket=getattr(payload, "bucket", None),
    )


def resolve_payload_time_range(payload):
    """Interpret offset-free user inputs in the configured plant timezone."""

    try:
        return normalise_query_range(payload.start_time, payload.stop_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _convert_scalar_from_benchmark(value, display_unit):
    if value is None:
        return None
    return float(convert_values([value], "bar", display_unit)[0])


def _convert_chart_rows(rows, keys, display_unit):
    if normalize_unit_name(display_unit) == "bar":
        return rows
    for row in rows or []:
        for key in keys:
            if row.get(key) is not None:
                row[key] = _convert_scalar_from_benchmark(row[key], display_unit)
    return rows


def convert_comparison_charts_for_display(cycle_results, continuous_chart, display_unit):
    """Convert chart pressure values only; similarity metrics stay canonical."""
    for result in cycle_results or []:
        _convert_chart_rows(
            result.get("overlay_chart"),
            ["realtime", "benchmark", "benchmark_upper", "benchmark_lower"],
            display_unit,
        )
        _convert_chart_rows(
            result.get("visual_overlay_chart"),
            ["realtime_smooth", "benchmark", "benchmark_upper", "benchmark_lower"],
            display_unit,
        )
        _convert_chart_rows(result.get("original_cycle_chart"), ["smooth"], display_unit)
    _convert_chart_rows(
        continuous_chart,
        ["realtime_smooth", "benchmark_overlay"],
        display_unit,
    )


def get_signal_value_col(df) -> str:
    """
    Select the correct pressure column for signal processing.

    Some dataframes already contain value_std.
    Some only contain value.
    If the dataframe is empty or does not contain usable pressure values,
    return a user-friendly no-data error instead of KeyError: 'value_std'.
    """
    if df is None or df.empty:
        raise ValueError("No data found for the selected time range and Data ID.")

    if "value_std" in df.columns and df["value_std"].notna().any():
        return "value_std"

    if "value" in df.columns and df["value"].notna().any():
        return "value"

    raise ValueError("No usable pressure values found for the selected time range and Data ID.")


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


def prepare_complete_cycles_for_request(payload, source):
    """Detect complete cycles with hidden boundary-confirmation padding."""

    start_time, stop_time = resolve_payload_time_range(payload)
    query_start, query_stop = pad_query_range(
        start_time,
        stop_time,
        CYCLE_QUERY_PADDING_MINUTES,
    )
    df = fetch_data(
        bucket=source["bucket"],
        measurement=source["measurement"],
        field=payload.field,
        tag_id=payload.tag_id,
        start_time=query_start,
        stop_time=query_stop,
        source_unit_fallback=source["source_unit"],
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
    )
    df, cycles_idx = restrict_cycles_to_time_range(
        df,
        cycles_idx,
        start_time,
        stop_time,
    )
    return (
        df,
        cycles_idx,
        baseline_value,
        baseline_margin,
        threshold,
        start_time,
        stop_time,
    )


def friendly_error_message(error: Exception) -> str:
    return public_friendly_error_message(error)


# ---------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------
@app.get("/")
def root():
    return {"message": "Sterilizer Benchmark API is running"}


@app.get("/api/health")
def health():
    """Backward-compatible deployment check; now reports real readiness."""

    return health_ready()


@app.get("/api/health/live")
def health_live():
    """Liveness: the API process can receive and answer requests."""

    return {
        "status": "ok",
        "service": "sterilizer-benchmark-api",
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


@app.get("/api/health/ready")
def health_ready():
    """Readiness: required databases are configured and reachable."""

    dependencies = {
        "authentication_db": check_auth_db_connectivity(),
        "supabase": check_supabase_connectivity(),
        "influxdb": check_influx_connectivity(),
        "pressure_measurements": check_pressure_measurement_configuration(),
    }
    ready = all(item.get("ok") for item in dependencies.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "service": "sterilizer-benchmark-api",
        "version": "v35",
        "dependencies": dependencies,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return payload if ready else JSONResponse(status_code=503, content=payload)


def check_pressure_measurement_configuration():
    try:
        return {
            "ok": bool(measurement_for_unit("bar") and measurement_for_unit("psi")),
        }
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}


# ---------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------
@app.post("/api/detect-cycles", response_model=DetectCyclesResponse)
def detect_cycles(payload: DetectCyclesRequest):
    validate_common_inputs(payload)

    try:
        source = resolve_payload_pressure_source(payload)
        (
            df,
            cycles_idx,
            baseline_value,
            baseline_margin,
            threshold,
            start_time,
            stop_time,
        ) = prepare_complete_cycles_for_request(payload, source)

        detection_fingerprint = build_detection_fingerprint(
            bucket=source["bucket"],
            measurement=source["measurement"],
            field=payload.field,
            tag_id=payload.tag_id,
            source_unit=source["source_unit"],
            start_time=start_time,
            stop_time=stop_time,
            smooth_window=payload.smooth_window,
            df=df,
            cycles_idx=cycles_idx,
        )

        cycles = build_cycle_summary(df, cycles_idx, value_col="smooth")

        source_unit = source["source_unit"]

        benchmark_unit = (
            df["benchmark_unit"].dropna().iloc[0]
            if "benchmark_unit" in df.columns and df["benchmark_unit"].notna().any()
            else None
        )

        display_smooth = convert_values(
            df["smooth"].astype(float).to_numpy(),
            benchmark_unit or "bar",
            source_unit,
        ).tolist()

        return {
            "times": [t.isoformat() for t in df["time"]],
            "values": df["value"].astype(float).tolist(),
            "smooth": [float(value) for value in display_smooth],
            "baseline_value": _convert_scalar_from_benchmark(baseline_value, source_unit),
            "baseline_margin": _convert_scalar_from_benchmark(baseline_margin, source_unit),
            "threshold": _convert_scalar_from_benchmark(threshold, source_unit),
            "cycles": cycles,
            "source_unit": source_unit,
            "benchmark_unit": benchmark_unit,
            "detection_fingerprint": detection_fingerprint,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise_http_error(e)


# ---------------------------------------------------------------------
# Benchmark generation
# ---------------------------------------------------------------------
@app.post("/api/build-benchmark", response_model=BenchmarkResponse)
def create_benchmark(payload: BuildBenchmarkRequest):
    validate_common_inputs(payload)

    if not payload.benchmark_name or not payload.benchmark_name.strip():
        raise HTTPException(status_code=400, detail="Benchmark Name is required.")

    try:
        source = resolve_payload_pressure_source(payload)
        (
            df,
            cycles_idx,
            _baseline_value,
            _baseline_margin,
            _threshold,
            start_time,
            stop_time,
        ) = prepare_complete_cycles_for_request(payload, source)

        actual_fingerprint = build_detection_fingerprint(
            bucket=source["bucket"],
            measurement=source["measurement"],
            field=payload.field,
            tag_id=payload.tag_id,
            source_unit=source["source_unit"],
            start_time=start_time,
            stop_time=stop_time,
            smooth_window=payload.smooth_window,
            df=df,
            cycles_idx=cycles_idx,
        )
        if (
            not payload.detection_fingerprint
            or payload.detection_fingerprint != actual_fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Detected cycles or detection parameters changed. "
                    "Run Detect Cycles again before building the benchmark."
                ),
            )
        try:
            selected_cycle_indices = selected_cycle_indices_from_boundaries(
                df,
                cycles_idx,
                payload.selected_cycle_boundaries,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        benchmark, normalized_cycles = build_benchmark(
            df=df,
            cycles_idx=cycles_idx,
            selected_cycle_indices=selected_cycle_indices,
            n_points=BENCHMARK_NORMALIZED_POINTS,
            value_col="smooth",
            benchmark_name=payload.benchmark_name,
        )

        file_name = save_benchmark_json(benchmark)
        benchmark["saved_path"] = file_name
        benchmark["file_name"] = file_name

        return {
            "benchmark": benchmark,
            "normalized_cycles": normalized_cycles,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise_http_error(e)


@app.get("/api/benchmarks", response_model=BenchmarkListResponse)
def get_benchmarks():
    try:
        items = list_benchmarks()
        return {"benchmarks": items}
    except Exception as e:
        raise_http_error(e)


@app.get("/api/benchmarks/{identifier}", response_model=BenchmarkLoadResponse)
def get_one_benchmark(identifier: str):
    try:
        benchmark = load_benchmark(identifier)
        return {"benchmark": benchmark}
    except Exception as e:
        raise_http_error(e, default_status=404)


@app.get(
    "/api/active-benchmark/{tag_id}/{field}",
    response_model=BenchmarkLoadResponse,
)
def get_active_benchmark(tag_id: str, field: str):
    """Load the Supabase-assigned active benchmark for one sterilizer."""

    try:
        settings = load_daily_report_settings()
        file_name = get_active_benchmark_file_name(settings, tag_id, field)
        if not file_name:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No active benchmark is assigned to {tag_id} {field}. "
                    "Open Settings and assign an active benchmark first."
                ),
            )

        benchmark = load_benchmark(file_name)
        validate_benchmark_compatibility(
            benchmark,
            tag_id=tag_id,
            field=field,
            file_name=file_name,
        )
        return {"benchmark": benchmark}
    except HTTPException:
        raise
    except Exception as e:
        raise_http_error(e, default_status=404)


# ---------------------------------------------------------------------
# AI Comparison / V4 comparison
# ---------------------------------------------------------------------
@app.post("/api/compare-cycles", response_model=CompareCyclesResponse)
def compare_cycles(payload: CompareCyclesRequest):
    validate_common_inputs(payload)

    try:
        source = resolve_payload_pressure_source(payload)
        start_time, stop_time = resolve_payload_time_range(payload)

        # The active benchmark configured in Supabase is authoritative.  Do not
        # reuse a benchmark merely because Benchmark Management previewed it in
        # the browser previously.  ``benchmark_file_name`` remains optional in
        # the request only for backward compatibility with older frontends.
        settings = load_daily_report_settings()
        active_benchmark_file = get_active_benchmark_file_name(
            settings, payload.tag_id, payload.field
        )
        if not active_benchmark_file:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No active benchmark is assigned to {payload.tag_id} "
                    f"{payload.field}. Open Settings and assign one first."
                ),
            )

        requested_benchmark_file = str(payload.benchmark_file_name or "").strip()
        if requested_benchmark_file and requested_benchmark_file != active_benchmark_file:
            LOGGER.info(
                "Ignoring stale/requested benchmark %s for %s %s; active Supabase "
                "assignment is %s.",
                requested_benchmark_file,
                payload.tag_id,
                payload.field,
                active_benchmark_file,
            )

        benchmark = load_benchmark(active_benchmark_file)
        validate_benchmark_compatibility(
            benchmark,
            tag_id=payload.tag_id,
            field=payload.field,
            file_name=active_benchmark_file,
        )

        (
            df,
            cycles_idx,
            _baseline_value,
            _baseline_margin,
            _threshold,
            _start_time,
            _stop_time,
        ) = prepare_complete_cycles_for_request(payload, source)

        if len(cycles_idx) == 0:
            raise ValueError("No complete cycles detected in the selected real-time range.")

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

        convert_comparison_charts_for_display(
            cycle_results,
            continuous_overlay_chart,
            source["source_unit"],
        )

        return {
            "benchmark_name": benchmark.get("benchmark_name"),
            "benchmark_file_name": active_benchmark_file,
            "benchmark_id": benchmark.get("benchmark_id") or benchmark.get("database_id"),
            "benchmark_field": benchmark.get("field"),
            "benchmark_unit": benchmark.get("benchmark_unit") or benchmark.get("unit"),
            "source_measurement": source["measurement"],
            "source_unit": source["source_unit"],
            "cycle_results": cycle_results,
            "continuous_overlay_chart": continuous_overlay_chart,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise_http_error(e)

# ---------------------------------------------------------------------
# Save adjusted benchmark
# ---------------------------------------------------------------------
class SaveAdjustedBenchmarkRequest(BaseModel):
    benchmark_name: str
    benchmark_curve: list[float]
    normalized_points: int | None = None
    unit: str | None = None
    measurement: str | None = None
    field: str | None = None
    tag_id: str | None = None
    id: str | None = None
    sterilizer_id: str | None = None
    benchmark_unit: str | None = None
    source_unit: str | None = None
    source_cycle_count: int | None = None
    source_cycles: list[dict] | None = None
    created_at: str | None = None
    adjusted_from: str | None = None
    adjusted_from_file_name: str | None = None
    anchor_indices: list[int] | None = None
    anchor_values: list[float] | None = None
    adjustment_reason: str | None = None


@app.post("/api/benchmarks/save-adjusted")
def save_adjusted_benchmark(payload: SaveAdjustedBenchmarkRequest) -> Dict[str, Any]:
    try:
        if not payload.benchmark_curve:
            raise HTTPException(status_code=400, detail="Benchmark curve is empty.")

        benchmark_name = payload.benchmark_name.strip()
        if not benchmark_name:
            raise HTTPException(status_code=400, detail="Benchmark name is required.")

        source_file_name = str(payload.adjusted_from_file_name or "").strip()
        if not source_file_name:
            raise HTTPException(
                status_code=400,
                detail="The original benchmark file is required before an adjusted benchmark can be saved.",
            )
        source_benchmark = load_benchmark(source_file_name)
        validate_benchmark_document(source_benchmark)
        source_tag_id = (
            source_benchmark.get("tag_id")
            or source_benchmark.get("sterilizer_id")
            or source_benchmark.get("id")
        )
        source_field = source_benchmark.get("field")
        if not source_tag_id or not source_field:
            raise ValueError(
                "The original benchmark is missing its Plant Data ID or sterilizer field metadata. "
                "Recreate or repair it before saving an adjustment."
            )
        validate_benchmark_compatibility(
            source_benchmark,
            tag_id=str(source_tag_id),
            field=str(source_field),
            file_name=source_file_name,
        )
        source_curve = source_benchmark.get("benchmark_curve") or []
        if len(payload.benchmark_curve) != len(source_curve):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Adjusted benchmark curve length must match the original benchmark "
                    f"({len(source_curve)} points)."
                ),
            )

        edited_curve = []
        for index, value in enumerate(payload.benchmark_curve):
            numeric = float(value)
            if not math.isfinite(numeric):
                raise HTTPException(
                    status_code=400,
                    detail=f"Adjusted benchmark point {index + 1} must be a finite number.",
                )
            edited_curve.append(numeric)

        anchor_indices = list(payload.anchor_indices or [])
        anchor_values = list(payload.anchor_values or [])
        if len(anchor_indices) != len(anchor_values):
            raise HTTPException(
                status_code=400,
                detail="Adjusted benchmark anchor positions and values must have the same length.",
            )
        if len(set(anchor_indices)) != len(anchor_indices):
            raise HTTPException(
                status_code=400,
                detail="Adjusted benchmark anchor positions must be unique.",
            )
        for anchor_index, anchor_value in zip(anchor_indices, anchor_values):
            if anchor_index < 0 or anchor_index >= len(edited_curve):
                raise HTTPException(
                    status_code=400,
                    detail="Adjusted benchmark contains an anchor outside the curve range.",
                )
            if not math.isfinite(float(anchor_value)):
                raise HTTPException(
                    status_code=400,
                    detail="Adjusted benchmark anchor values must be finite numbers.",
                )

        benchmark_unit = source_benchmark.get("benchmark_unit") or source_benchmark.get("unit") or "bar"
        source_unit = source_benchmark.get("source_unit") or benchmark_unit
        tag_id = (
            source_benchmark.get("tag_id")
            or source_benchmark.get("sterilizer_id")
            or source_benchmark.get("id")
        )

        benchmark_data = {
            "benchmark_name": benchmark_name,
            "benchmark_curve": edited_curve,
            "benchmark_std": source_benchmark.get("benchmark_std"),
            "normalized_points": len(edited_curve),
            "unit": benchmark_unit,
            "benchmark_unit": benchmark_unit,
            "source_unit": source_unit,
            "conversion_applied": bool(source_benchmark.get("conversion_applied")),
            "measurement": source_benchmark.get("measurement"),
            "field": source_benchmark.get("field"),
            "id": tag_id,
            "tag_id": tag_id,
            "sterilizer_id": tag_id,
            "source_cycle_count": source_benchmark.get("source_cycle_count"),
            "source_cycles": source_benchmark.get("source_cycles") or [],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "adjusted_from": source_benchmark.get("benchmark_name"),
            "adjusted_from_file_name": source_file_name,
            "anchor_indices": anchor_indices,
            "anchor_values": [float(value) for value in anchor_values],
            "adjustment_reason": str(payload.adjustment_reason or "Manual curve adjustment").strip(),
            "value_column_used": "manual_adjusted_curve",
        }

        validate_benchmark_document(benchmark_data)
        file_name = save_benchmark_json(benchmark_data)
        audit_result = try_record_audit_event(
            action="benchmark_adjusted",
            entity_type="benchmark",
            entity_id=file_name,
            before=benchmark_audit_summary(source_benchmark),
            after=benchmark_audit_summary(benchmark_data),
            details={
                "source_file_name": source_file_name,
                "adjustment_reason": benchmark_data["adjustment_reason"],
                "anchor_count": len(anchor_indices),
            },
        )

        return {
            "message": "Adjusted benchmark saved successfully.",
            "audit_status": audit_result["status"],
            "file_name": file_name,
            "benchmark": benchmark_data,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise_http_error(e)


# ---------------------------------------------------------------------
# Tag / sterilizer mapping
# ---------------------------------------------------------------------
@app.get("/api/tags")
def list_tags(unit: str = "bar", refresh: bool = False):
    try:
        source = get_pressure_source(unit)
        tags = get_all_tags(source_unit=source["source_unit"], refresh=refresh)
        data_ids = [tag.get("tag_id") for tag in tags if tag.get("tag_id")]
        official_names = load_official_plant_names(data_ids)
        custom_aliases = load_plant_custom_aliases(data_ids)
        for tag in tags:
            details = get_plant_name_details(
                DEFAULT_DAILY_REPORT_SETTINGS,
                tag.get("tag_id"),
                official_names=official_names,
                custom_aliases=custom_aliases,
            )
            tag.update(details)
            tag["tag_name"] = details["display_name"]
        return {
            "tags": tags,
            "measurement": source["measurement"],
            "unit": source["source_unit"],
        }
    except Exception as e:
        raise_http_error(e)


@app.get("/api/tag-sterilizers/{tag_id}")
def get_tag_sterilizers(tag_id: str, unit: str = "bar", refresh: bool = False):
    try:
        source = get_pressure_source(unit)
        sterilizers = get_sterilizers_for_tag(
            tag_id, source_unit=source["source_unit"], refresh=refresh
        )
        if not sterilizers:
            raise HTTPException(status_code=404, detail="No sterilizers found for selected tag.")
        return {
            "tag_id": tag_id,
            "measurement": source["measurement"],
            "unit": source["source_unit"],
            "sterilizers": sterilizers,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise_http_error(e)


# ---------------------------------------------------------------------
# Daily Report settings and generation
# ---------------------------------------------------------------------
def _daily_report_configuration_payload(refresh: bool = False):
    settings, recovery = load_daily_report_settings_with_status()
    benchmarks = list_benchmarks()
    grouped_catalog = {}
    discovery_error = None

    try:
        tags = get_all_tags(source_unit="bar", refresh=refresh)
        for tag in tags:
            data_id = tag["tag_id"]
            site_code = derive_site_code(data_id)
            saved_site = get_site_config(settings, site_code) or {}
            site_entry = grouped_catalog.setdefault(
                site_code,
                {
                    "site_code": site_code,
                    "display_name": get_site_display_name(settings, site_code),
                    "shifts": deepcopy(
                        saved_site.get("shifts") or DEFAULT_SHIFT_SETTINGS
                    ),
                    "plants": [],
                },
            )
            site_entry["plants"].append(
                {
                    "data_id": data_id,
                    "sterilizers": get_sterilizers_for_tag(
                        data_id, source_unit="bar", refresh=False
                    ),
                }
            )
    except Exception as exc:
        discovery_error = friendly_error_message(exc)

    # Preserve configured sites/plants even during a temporary InfluxDB outage.
    for saved_site in settings.get("sites") or []:
        site_code = saved_site.get("site_code")
        if not site_code:
            continue
        site_entry = grouped_catalog.setdefault(
            site_code,
            {
                "site_code": site_code,
                "display_name": saved_site.get("display_name") or site_code,
                "shifts": deepcopy(
                    saved_site.get("shifts") or DEFAULT_SHIFT_SETTINGS
                ),
                "plants": [],
                "metadata_status": "not_currently_discovered",
            },
        )
        known_plant_ids = {item["data_id"] for item in site_entry["plants"]}
        for saved_plant in saved_site.get("plants") or []:
            data_id = saved_plant.get("data_id")
            if data_id and data_id not in known_plant_ids:
                site_entry["plants"].append(
                    {
                        "data_id": data_id,
                        "sterilizers": [],
                        "metadata_status": "not_currently_discovered",
                    }
                )

    catalog = list(grouped_catalog.values())
    catalog_data_ids = [
        plant.get("data_id")
        for site in catalog
        for plant in site.get("plants") or []
        if plant.get("data_id")
    ]
    official_names = load_official_plant_names(catalog_data_ids)
    for site in catalog:
        for plant in site.get("plants") or []:
            plant.update(
                get_plant_name_details(
                    settings,
                    plant.get("data_id"),
                    official_names=official_names,
                )
            )
        site["plants"].sort(
            key=lambda item: (item.get("display_name") or item["data_id"]).lower()
        )
    catalog.sort(
        key=lambda item: (item.get("display_name") or item["site_code"]).lower()
    )

    return {
        "settings": settings,
        "catalog": catalog,
        "benchmarks": benchmarks,
        "discovery_error": discovery_error,
        "settings_recovery": recovery,
    }


@app.get("/api/daily-report/settings")
def get_daily_report_settings(refresh: bool = False):
    try:
        return _daily_report_configuration_payload(refresh=refresh)
    except Exception as e:
        raise_http_error(e)


@app.put("/api/daily-report/settings")
def update_daily_report_settings(payload: DailyReportSettingsRequest):
    try:
        # Preserve the distinction between an omitted legacy alias field and
        # a deliberately cleared custom alias.
        saved = save_daily_report_settings(payload.dict(exclude_unset=True))
        audit_status = saved.pop("_audit_status", "recorded")
        return {
            "message": "Daily Report settings saved successfully.",
            "settings": saved,
            "audit_status": audit_status,
        }
    except Exception as e:
        raise_http_error(e)


@app.post("/api/daily-report/generate")
def create_daily_report(payload: DailyReportGenerateRequest):
    try:
        settings = load_daily_report_settings()
        return generate_daily_report(
            site_code=payload.site_code,
            data_id=payload.data_id,
            report_date_value=payload.report_date,
            settings=settings,
            smooth_window=payload.smooth_window,
        )
    except Exception as e:
        raise_http_error(e)


# ---------------------------------------------------------------------
# Live monitoring
# ---------------------------------------------------------------------
@app.post("/api/live-monitor")
def live_monitor(payload: LiveMonitorRequest):
    api_started = time.perf_counter()

    try:
        validate_measurement_matches_unit(payload.measurement, payload.source_unit)
        source = resolve_payload_pressure_source(payload)
        sterilizers = get_sterilizers_for_tag(
            payload.tag_id, source_unit=source["source_unit"]
        )

        if not sterilizers:
            raise ValueError("No sterilizers found for selected tag.")

        selected_hours = int(payload.last_n_hours or 3)

        if selected_hours not in [3, 6, 12, 24]:
            raise ValueError("Live monitoring time range must be 3, 6, 12, or 24 hours.")

        start_time, stop_time = get_live_window_iso(
            start_time=payload.start_time,
            stop_time=payload.stop_time,
            last_n_hours=selected_hours,
        )

        is_custom_range = bool(str(payload.start_time or "").strip())
        window_hours = (
            get_live_window_hours(start_time, stop_time)
            if is_custom_range
            else float(selected_hours)
        )
        aggregate_every = get_live_aggregate_window(window_hours)
        range_description = (
            f"the selected range from {start_time} to {stop_time}"
            if is_custom_range
            else f"the latest {selected_hours} hours"
        )

        fields = [item["field"] for item in sterilizers]
        field_to_name = {
            item["field"]: item["sterilizer_name"]
            for item in sterilizers
        }

        query_started = time.perf_counter()

        all_df = fetch_multi_field_data(
            bucket=source["bucket"],
            measurement=source["measurement"],
            fields=fields,
            tag_id=payload.tag_id,
            start_time=start_time,
            stop_time=stop_time,
            source_unit_fallback=source["source_unit"],
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
                        "error": f"No data found in {range_description}.",
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
                        complete_only=False,
                    )

                    sterilizer_result = build_live_sterilizer_payload(
                        df=df,
                        cycles_idx=cycles_idx,
                        sterilizer_name=sterilizer_name,
                        field=field,
                        display_unit=source["source_unit"],
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
            "measurement": source["measurement"],
            "source_unit": source["source_unit"],
            "start_time": start_time,
            "stop_time": stop_time,
            "updated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
            "range_mode": "custom" if is_custom_range else "latest",
            "window_hours_used": round(window_hours, 3),
            "window_minutes_used": round(window_hours * 60, 1),
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
        raise_http_error(e)

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
    metric_description: Optional[str] = ""
    warn_low: float
    critical_value: float
    recommendation_en: str
    rec_key: Optional[str] = None
    recommendation_bm: str
    urgency: Optional[str] = "High"
    target_metric: Optional[str] = None
    include_for_anomaly_retrieval: bool = True
    allow_conflict: bool = False


@app.get("/api/rca-rules")
def get_rca_rules(include_original: bool = True):
    """
    List original Excel-derived rules and custom rules.

    Both original and custom rules are editable from the management page.
    Supabase is the authoritative storage for both rule types; Qdrant remains
    the rebuildable vector search index.
    """
    try:
        return list_rca_rules(include_original=include_original)
    except Exception as e:
        raise_http_error(e)


@app.post("/api/rca-rules")
def create_rca_rule(payload: RcaRuleRequest):
    """
    Create one custom RCA rule and its one-to-one recommendation.
    The rule is saved to Supabase first, then its chunk is upserted into Qdrant.
    """
    try:
        rule, reindex_result = create_custom_rule(payload.dict())
        verification = verify_rule_installation(rule.get("rule_id"))
        return {
            "message": (
                "Custom analysis rule was saved, but the search index update needs attention."
                if reindex_result.get("status") == "index_failed"
                else "Custom analysis rule created successfully."
            ),
            "storage_status": "saved",
            "rule": rule,
            "qdrant_reindex": reindex_result,
            "verification": verification,
        }
    except RuleConflictWarning as conflict:
        raise HTTPException(status_code=409, detail=conflict.to_response())
    except Exception as e:
        raise_http_error(e)


@app.post("/api/rca-rules/reindex-custom")
def reindex_custom_rca_rules():
    """
    Backward-compatible endpoint. Reindexes custom rules only.
    """
    try:
        return reindex_all_custom_rules_to_qdrant()
    except Exception as e:
        raise_http_error(e)


@app.post("/api/rca-rules/reindex-all")
def reindex_all_rca_rules():
    """
    Rebuild Qdrant from the authoritative Supabase rules/reference chunks.
    """
    try:
        return reindex_all_rules_to_qdrant()
    except Exception as e:
        raise_http_error(e)


@app.get("/api/rca-rules/{rule_id}/verify")
def verify_rca_rule(rule_id: str):
    """
    Verify that a rule is saved in Supabase, indexed in Qdrant if possible,
    and directly available to RCA retrieval.
    This lets the UI confirm the Save button completed the full pipeline.
    """
    try:
        return verify_rule_installation(rule_id)
    except Exception as e:
        raise_http_error(e)


@app.put("/api/rca-rules/{rule_id}")
def update_rca_rule(rule_id: str, payload: RcaRuleRequest):
    """
    Update an existing RCA rule. Original Excel-derived rules and custom rules are both editable.
    """
    try:
        rule, reindex_result = update_custom_rule(rule_id, payload.dict())
        verification = verify_rule_installation(rule.get("rule_id"))
        return {
            "message": (
                "Analysis rule was saved, but the search index update needs attention."
                if reindex_result.get("status") == "index_failed"
                else "Analysis rule updated successfully."
            ),
            "storage_status": "saved",
            "rule": rule,
            "qdrant_reindex": reindex_result,
            "verification": verification,
        }
    except RuleConflictWarning as conflict:
        raise HTTPException(status_code=409, detail=conflict.to_response())
    except Exception as e:
        raise_http_error(e)


@app.delete("/api/rca-rules/{rule_id}")
def delete_rca_rule(rule_id: str):
    """
    Delete an existing RCA rule from Supabase and remove its Qdrant point.
    The source Excel file remains an offline backup/import source.
    """
    try:
        qdrant_delete = delete_custom_rule(rule_id)
        return {
            "message": "Analysis rule deleted successfully.",
            "delete_result": qdrant_delete,
        }
    except Exception as e:
        raise_http_error(e)
