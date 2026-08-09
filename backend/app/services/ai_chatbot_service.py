"""
AI chatbot service for sterilizer cycle analysis.

Purpose
-------
This service powers a bottom-right chatbot that can answer questions such as:
- "Check SKPG Sterilizer 3 from 24 June 2026 1pm to 3pm."
- "Is Stage 2 of Cycle 2 abnormal?"
- "Analyse ch4 from SAMYSK_PSTR_240004 between 2026-06-24 13:00 and 15:00."

Design
------
The chatbot is intentionally NOT a free-form diagnosing LLM. It uses the same
existing deterministic cycle comparison + RCA/RAG pipeline as the detail page.
The LLM is used only inside the RCA feedback generator for explanation wording.

Flow
----
1. Parse user natural language for tag/sterilizer/time/stage/cycle/benchmark.
2. If a new time range is provided, run comparison and summarize all cycles.
3. If user selects one cycle, check normal/anomaly using current threshold (<75).
4. If anomaly, call the existing RCA pipeline automatically:
   - no stage specified -> cycle-level RCA
   - stage specified -> stage-focused RCA
5. Return a human-friendly text reply plus hidden context for frontend memory.

Important datetime fix
----------------------
InfluxDB Flux time parsing is strict. Natural-language dates such as
"24 June 2026 1pm" are converted into RFC3339 timestamps with timezone offset,
for example:

    2026-06-24T13:00:00+08:00

This avoids Flux errors such as:

    cannot parse date time
    cannot convert string to time
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser as date_parser

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from app.services.influx_service import fetch_data
from app.services.signal_service import prepare_signal
from app.services.cycle_service import detect_full_cycles, refine_cycle_boundaries
from app.services.benchmark_service import list_benchmarks, load_benchmark
from app.services.comparison_service import compare_detected_cycles_to_benchmark
from app.rag.rca_pipeline import run_rca_feedback_pipeline


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
DEFAULT_BUCKET = os.getenv("CHATBOT_DEFAULT_BUCKET", "Mill")
DEFAULT_MEASUREMENT = os.getenv("CHATBOT_DEFAULT_MEASUREMENT", "PSTR")
DEFAULT_SMOOTH_WINDOW = int(os.getenv("CHATBOT_DEFAULT_SMOOTH_WINDOW", "9"))
ANOMALY_SCORE_THRESHOLD = float(os.getenv("CHATBOT_ANOMALY_SCORE_THRESHOLD", "75"))

# Important for natural-language dates. Malaysia local time is UTC+8.
# You can change this in .env if needed:
# CHATBOT_LOCAL_TIMEZONE=Asia/Kuala_Lumpur
CHATBOT_LOCAL_TIMEZONE = os.getenv("CHATBOT_LOCAL_TIMEZONE", "Asia/Kuala_Lumpur")
CHATBOT_LOCAL_UTC_OFFSET_HOURS = float(os.getenv("CHATBOT_LOCAL_UTC_OFFSET_HOURS", "8"))


# Short-name map based on your existing sterilizer display mapping.
# This lets users type "SKPG Sterilizer 3" instead of technical tag/field.
TAG_STERILIZER_DISPLAY_MAP: Dict[str, Dict[str, Any]] = {
    "SKPG": {
        "tag_id": "SAMYSK_PSTR_240004",
        "unit": "psi",
        "sterilizers": {
            1: "ch2",
            2: "ch3",
            3: "ch4",
            4: "ch5",
        },
    },
    "SKSPAD": {
        "tag_id": "SAMYSK_PSTR_250041",
        "unit": "bar",
        "sterilizers": {
            1: "ch2",
            2: "ch3",
            3: "ch4",
            4: "ch5",
        },
    },
    "SKRSB": {
        "tag_id": "SAMYSK_PSTR_240014",
        "unit": "psi",
        "sterilizers": {
            1: "ch3",
            2: "ch4",
            3: "ch5",
            4: "ch6",
            5: "ch7",
        },
    },
    "SKRHL": {
        "tag_id": "SAMYSK_PSTR_240006",
        "unit": "bar",
        "sterilizers": {
            1: "ch3",
            2: "ch4",
        },
    },
    "SKBAR": {
        "tag_id": "SAMYSK_PSTR_250036",
        "unit": "psi",
        "sterilizers": {
            1: "ch3",
            2: "ch4",
            3: "ch5",
            4: "ch6",
            5: "ch7",
            6: "ch8",
        },
    },
    "SKMJ": {
        "tag_id": "SAMYSK_PSTR_250024",
        "unit": "psi",
        "sterilizers": {
            1: "ch3",
            2: "ch4",
            3: "ch5",
        },
    },
}


STAGE_SYNONYMS: Dict[str, List[str]] = {
    "S1": ["s1", "stage 1", "stage one", "first stage", "air purging", "air purge", "purging"],
    "S2": ["s2", "stage 2", "stage two", "second stage", "fruitlet", "fruitlet loosening"],
    "S3": ["s3", "stage 3", "stage three", "third stage", "holding", "holding stage", "sterilization", "sterilisation"],
}


@dataclass
class ParsedChatRequest:
    bucket: Optional[str] = None
    measurement: Optional[str] = None
    tag_id: Optional[str] = None
    field: Optional[str] = None
    source_unit: Optional[str] = None
    start_time: Optional[str] = None
    stop_time: Optional[str] = None
    stage: Optional[str] = None
    cycle_no: Optional[int] = None
    benchmark_file_name: Optional[str] = None
    intent: str = "analyze"


# -----------------------------------------------------------------------------
# Basic helpers
# -----------------------------------------------------------------------------
def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def format_score(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.2f}"


def score_band(score: Any) -> str:
    value = safe_float(score)
    if value is None:
        return "Unknown"
    if value >= 90:
        return "Excellent"
    if value >= 75:
        return "Good"
    if value >= 60:
        return "Fair"
    if value >= 50:
        return "Poor"
    return "Critical"


def is_anomaly_score(score: Any) -> bool:
    value = safe_float(score)
    if value is None:
        return True
    return value < ANOMALY_SCORE_THRESHOLD


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def get_chatbot_timezone():
    """Return timezone for natural-language chatbot date parsing."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(CHATBOT_LOCAL_TIMEZONE)
        except Exception:
            pass
    return timezone(timedelta(hours=CHATBOT_LOCAL_UTC_OFFSET_HOURS))


def _strip_wrapping_quotes(value: Any) -> Optional[str]:
    """Remove accidental quote layers: '"2026..."' -> '2026...'"""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    for _ in range(6):
        text = text.strip()

        if text.startswith('\\"') and text.endswith('\\"') and len(text) >= 4:
            text = text[2:-2].strip()
            continue

        if len(text) >= 2 and (
            (text[0] == '"' and text[-1] == '"') or
            (text[0] == "'" and text[-1] == "'")
        ):
            try:
                decoded = json.loads(text) if text[0] == '"' else text[1:-1]
            except Exception:
                decoded = text[1:-1]
            decoded = str(decoded).strip()
            if decoded == text:
                break
            text = decoded
            continue

        break

    text = text.replace('\\"', '"').strip().rstrip(".。")

    if len(text) >= 2 and (
        (text[0] == '"' and text[-1] == '"') or
        (text[0] == "'" and text[-1] == "'")
    ):
        text = text[1:-1].strip()

    return text or None


def _ensure_timezone(dt: datetime) -> datetime:
    """Attach local timezone to naive chatbot datetimes."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=get_chatbot_timezone())
    return dt


def _format_rfc3339_for_influx(dt: datetime) -> str:
    """Return RFC3339 timestamp accepted by Flux time(v: ...)."""
    dt = _ensure_timezone(dt)
    # Keep local offset instead of converting to UTC so logs/UI remain easier
    # to compare with user input. Flux accepts RFC3339 offsets like +08:00.
    return dt.isoformat(timespec="seconds")


def sanitize_datetime_for_influx(value: Any) -> Optional[str]:
    """
    Return a clean RFC3339 datetime string for InfluxDB/Flux.

    Examples
    --------
    Input:  '24 June 2026 1pm'
    Output: '2026-06-24T13:00:00+08:00'

    Input:  '"2026-06-24T13:00:00"'
    Output: '2026-06-24T13:00:00+08:00'
    """
    text = _strip_wrapping_quotes(value)
    if not text:
        return None

    # Keep Flux relative expressions untouched if you add them later.
    lowered = text.lower()
    if lowered.startswith("-") or lowered.startswith("now()"):
        return text

    try:
        dt = date_parser.parse(text, fuzzy=True)
        return _format_rfc3339_for_influx(dt)
    except Exception:
        # Return cleaned value so the caller can still show a helpful error.
        return text


def friendly_chatbot_error_message(error: Exception) -> str:
    """Convert verbose backend/Influx errors into chatbot-friendly text."""
    message = str(error)
    lower = message.lower()

    if "cannot convert string" in lower and "time" in lower:
        return (
            "The start or end time could not be understood by InfluxDB. "
            "Please use a clear time format, for example: "
            "Check SKPG Sterilizer 3 from 2026-06-24 13:00 to 2026-06-24 15:00."
        )

    if "cannot parse date time" in lower or "invalid date" in lower or "cannot parse date" in lower:
        return (
            "The date/time format is invalid. Please try a clearer format such as "
            "2026-06-24 13:00 to 2026-06-24 15:00."
        )

    if "no complete cycle" in lower:
        return "No complete sterilizer cycle was detected in that time range. Please choose a wider time range."

    if "no data" in lower or "no usable pressure" in lower:
        return "No usable pressure data was found for the selected sterilizer and time range."

    if "benchmark" in lower and "not found" in lower:
        return "The requested benchmark was not found. Please build or select a valid benchmark first."

    # Try to extract the actual InfluxDB JSON error body if present.
    match = re.search(r'"message"\s*:\s*"([^"]+)"', message)
    if match:
        return match.group(1)

    return message


def strip_big_chart_fields(cycle: Dict[str, Any]) -> Dict[str, Any]:
    """Keep chatbot context lightweight."""
    item = copy.deepcopy(cycle)
    for key in [
        "visual_overlay_chart",
        "overlay_chart",
        "original_cycle_chart",
        "continuous_overlay_chart",
    ]:
        item.pop(key, None)
    return item


def compact_context(context: Dict[str, Any]) -> Dict[str, Any]:
    ctx = dict(context or {})
    cycles = ctx.get("cycle_results") or []
    ctx["cycle_results"] = [strip_big_chart_fields(c) for c in cycles]
    return ctx


# -----------------------------------------------------------------------------
# Natural-language parsing
# -----------------------------------------------------------------------------
def parse_stage(message: str) -> Optional[str]:
    lower = message.lower()
    for stage, terms in STAGE_SYNONYMS.items():
        for term in terms:
            if re.search(rf"\b{re.escape(term)}\b", lower):
                return stage
    return None


def parse_cycle_no(message: str) -> Optional[int]:
    patterns = [
        r"\bcycle\s*(\d+)\b",
        r"\bcycle\s*#\s*(\d+)\b",
        r"\bc(\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None
    return None


def parse_tag_and_field(message: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return tag_id, field, source_unit if detected."""
    text = message.strip()
    upper = text.upper()

    # Short name form: "SKPG Sterilizer 3", "SKPG ST3", "SKPG 3"
    for short_name, info in TAG_STERILIZER_DISPLAY_MAP.items():
        if short_name not in upper:
            continue

        # Prefer explicit Sterilizer N / ST N.
        m = re.search(
            rf"\b{re.escape(short_name)}\b\s*(?:sterilizer|steriliser|st)?\s*[-_ ]?\s*(\d+)",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            # Also allow "Sterilizer 3 SKPG"
            m = re.search(
                rf"(?:sterilizer|steriliser|st)\s*[-_ ]?\s*(\d+).*\b{re.escape(short_name)}\b",
                text,
                flags=re.IGNORECASE,
            )

        if m:
            number = int(m.group(1))
            field = info["sterilizers"].get(number)
            if field:
                return info["tag_id"], field, info.get("unit")

        # If only short name is given, return tag only and ask for sterilizer number.
        return info["tag_id"], None, info.get("unit")

    # Technical tag ID and field.
    tag_match = re.search(r"\bSAMYSK_PSTR_\d+\b", text, flags=re.IGNORECASE)
    field_match = re.search(r"\bch\s*(\d+)\b", text, flags=re.IGNORECASE)

    tag_id = tag_match.group(0).upper() if tag_match else None
    field = f"ch{field_match.group(1)}" if field_match else None

    return tag_id, field, None


def parse_benchmark_name(message: str) -> Optional[str]:
    # Supports "using benchmark X" or "use benchmark X".
    m = re.search(r"\b(?:using|use)\s+benchmark\s+(.+)$", message, flags=re.IGNORECASE)
    if not m:
        return None

    value = m.group(1).strip().strip(".。")
    # Stop before common time keywords if user wrote extra text after benchmark.
    value = re.split(r"\b(?:from|between|for)\b", value, flags=re.IGNORECASE)[0].strip()
    return value or None


def _parse_dt(value: str, default: Optional[datetime] = None) -> datetime:
    """Parse a natural-language datetime and attach chatbot local timezone."""
    if default is None:
        default = datetime.now(tz=get_chatbot_timezone()).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    default = _ensure_timezone(default)
    dt = date_parser.parse(value, fuzzy=True, default=default)
    return _ensure_timezone(dt)


def _cleanup_datetime_phrase(text: str) -> str:
    """Remove words that confuse dateutil when extracting a date phrase."""
    value = str(text or "").strip()
    value = re.sub(r"^(?:check|analyse|analyze|please|can you|could you)\s+", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"\b(?:using|use)\s+benchmark\s+.+$", "", value, flags=re.IGNORECASE).strip()
    value = value.strip(" .。?\n\t")
    return value


def parse_time_range(message: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse common ranges and return RFC3339 strings accepted by InfluxDB."""
    text = normalize_text(message)

    # Remove benchmark tail because it can confuse dateutil.
    text = re.sub(r"\b(?:using|use)\s+benchmark\s+.+$", "", text, flags=re.IGNORECASE).strip()

    # between X and Y
    m = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:$|\?|\.)", text, flags=re.IGNORECASE)
    if not m:
        # from X to Y
        m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:$|\?|\.)", text, flags=re.IGNORECASE)

    if not m:
        return None, None

    start_raw = _cleanup_datetime_phrase(m.group(1))
    stop_raw = _cleanup_datetime_phrase(m.group(2))

    try:
        start_dt = _parse_dt(start_raw)
        # For stop time like "3pm", use the start date as default.
        stop_default = start_dt.replace(second=0, microsecond=0)
        stop_dt = _parse_dt(stop_raw, default=stop_default)

        if stop_dt <= start_dt:
            stop_dt += timedelta(days=1)

        return (
            _format_rfc3339_for_influx(start_dt),
            _format_rfc3339_for_influx(stop_dt),
        )
    except Exception:
        return None, None


def detect_intent(message: str) -> str:
    lower = message.lower()
    if any(word in lower for word in ["adjust", "solve", "possible", "what if", "how if", "improve"]):
        return "what_if"
    return "analyze"


def parse_chat_message(message: str) -> ParsedChatRequest:
    tag_id, field, unit = parse_tag_and_field(message)
    start_time, stop_time = parse_time_range(message)

    return ParsedChatRequest(
        bucket=DEFAULT_BUCKET,
        measurement=DEFAULT_MEASUREMENT,
        tag_id=tag_id,
        field=field,
        source_unit=unit,
        start_time=start_time,
        stop_time=stop_time,
        stage=parse_stage(message),
        cycle_no=parse_cycle_no(message),
        benchmark_file_name=parse_benchmark_name(message),
        intent=detect_intent(message),
    )


# -----------------------------------------------------------------------------
# Benchmark and comparison
# -----------------------------------------------------------------------------
def find_default_benchmark(
    *,
    tag_id: Optional[str],
    field: Optional[str],
    explicit_name: Optional[str] = None,
) -> Dict[str, Any]:
    items = list_benchmarks()
    if not items:
        raise ValueError("No benchmark files were found. Please build a benchmark first.")

    if explicit_name:
        query = explicit_name.lower().replace(".json", "").strip()
        for item in items:
            file_name = str(item.get("file_name") or "")
            benchmark_name = str(item.get("benchmark_name") or "")
            if (
                query == file_name.lower().replace(".json", "")
                or query in file_name.lower()
                or query in benchmark_name.lower()
            ):
                benchmark = load_benchmark(file_name)
                benchmark["file_name"] = file_name
                return benchmark

        raise ValueError(f"Benchmark '{explicit_name}' was not found.")

    def score_item(item: Dict[str, Any]) -> int:
        score = 0
        item_tag = str(item.get("id") or item.get("sterilizer_id") or "").strip()
        item_field = str(item.get("field") or "").strip().lower()
        if tag_id and item_tag == tag_id:
            score += 10
        if field and item_field == str(field).lower():
            score += 5
        return score

    ranked = sorted(items, key=score_item, reverse=True)
    best = ranked[0]

    benchmark = load_benchmark(best["file_name"])
    benchmark["file_name"] = best["file_name"]
    benchmark["_match_score"] = score_item(best)
    return benchmark


def get_signal_value_col(df) -> str:
    if df is None or df.empty:
        raise ValueError("No data found for the selected time range and sterilizer.")
    if "value_std" in df.columns and df["value_std"].notna().any():
        return "value_std"
    if "value" in df.columns and df["value"].notna().any():
        return "value"
    raise ValueError("No usable pressure values were found for the selected time range.")


def prepare_df_for_analysis(df, smooth_window: int):
    value_col = get_signal_value_col(df)
    return prepare_signal(df, smooth_window=smooth_window, value_col=value_col)


def run_cycle_comparison(
    *,
    bucket: str,
    measurement: str,
    tag_id: str,
    field: str,
    start_time: str,
    stop_time: str,
    source_unit: str,
    benchmark_file_name: Optional[str] = None,
    smooth_window: int = DEFAULT_SMOOTH_WINDOW,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    start_time = sanitize_datetime_for_influx(start_time)
    stop_time = sanitize_datetime_for_influx(stop_time)

    if not start_time or not stop_time:
        raise ValueError("Start time and stop time are required for chatbot analysis.")

    benchmark = find_default_benchmark(
        tag_id=tag_id,
        field=field,
        explicit_name=benchmark_file_name,
    )

    df = fetch_data(
        bucket=bucket,
        measurement=measurement,
        field=field,
        tag_id=tag_id,
        start_time=start_time,
        stop_time=stop_time,
        source_unit_fallback=source_unit,
    )

    df, baseline_value, baseline_margin = prepare_df_for_analysis(
        df,
        smooth_window=smooth_window,
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

    if not cycles_idx:
        raise ValueError("No complete cycle was detected in the selected time range.")

    cycle_results = compare_detected_cycles_to_benchmark(
        df=df,
        cycles_idx=cycles_idx,
        benchmark=benchmark,
        value_col="smooth",
    )

    compact_cycles = [strip_big_chart_fields(c) for c in cycle_results]
    return compact_cycles, benchmark


# -----------------------------------------------------------------------------
# RCA and text response
# -----------------------------------------------------------------------------
def get_stage_score_from_cycle(cycle: Dict[str, Any], stage: str) -> Optional[float]:
    sb = cycle.get("score_breakdown") or {}
    return safe_float(sb.get(f"{stage.lower()}_score"))


def get_cycle_by_no(cycles: List[Dict[str, Any]], cycle_no: int) -> Optional[Dict[str, Any]]:
    for cycle in cycles:
        if int(cycle.get("cycle_no") or -1) == int(cycle_no):
            return cycle
    return None


def enrich_cycle_for_rca(
    *,
    cycle: Dict[str, Any],
    context: Dict[str, Any],
    stage: Optional[str],
) -> Dict[str, Any]:
    scoring = copy.deepcopy(cycle)
    metrics = dict(scoring.get("metrics") or {})

    metrics.update(
        {
            "bucket": context.get("bucket"),
            "measurement": context.get("measurement"),
            "tag_id": context.get("tag_id"),
            "field": context.get("field"),
            "source_unit": context.get("source_unit"),
            "cycle_start": cycle.get("cycle_start"),
            "cycle_end": cycle.get("cycle_end"),
            "start_time": context.get("start_time"),
            "stop_time": context.get("stop_time"),
        }
    )

    if stage:
        metrics["user_selected_stage"] = stage
        metrics["affected_stage"] = stage
    else:
        metrics.pop("user_selected_stage", None)

    scoring["metrics"] = metrics
    return scoring


def _as_list(value: Any) -> List[str]:
    """Convert a backend value into a clean list of strings."""
    if value is None:
        return []

    if isinstance(value, list):
        output: List[str] = []
        for item in value:
            output.extend(_as_list(item))
        return output

    if isinstance(value, tuple):
        output: List[str] = []
        for item in value:
            output.extend(_as_list(item))
        return output

    if isinstance(value, dict):
        if value.get("body") is not None:
            return _as_list(value.get("body"))
        if value.get("items") is not None:
            return _as_list(value.get("items"))
        return []

    text = str(value).strip()
    if not text:
        return []

    # Keep bullet/action lists separate, but also support normal paragraphs.
    lines = [line.strip(" -•\t") for line in re.split(r"\n+", text) if line.strip()]
    return [line for line in lines if line]


def _unique_clean_list(values: List[str]) -> List[str]:
    """Keep order while removing repeated/empty strings."""
    output: List[str] = []
    seen = set()
    for value in values:
        clean = normalize_text(value).strip(" -•\t")
        if not clean:
            continue
        key = clean.lower().rstrip(".")
        if key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return output


def _plain_stage_name(stage: Optional[str]) -> str:
    return {
        "S1": "first pressurisation phase",
        "S2": "second pressurisation phase",
        "S3": "pressure holding phase",
    }.get(str(stage or "").upper(), "the selected cycle period")


def _clean_chatbot_sentence(value: Any) -> str:
    """Clean wording for a short chatbot RCA cause/action sentence."""
    text = normalize_text(str(value or ""))
    if not text:
        return ""

    replacements = [
        (r"\bbenchmark\b", "normal operating profile"),
        (r"\bthreshold\b", "limit"),
        (r"\bdeviation\b", "difference from normal"),
        (r"\bMAE_[A-Za-z0-9_]+\b", "pressure difference"),
        (r"\bRMSE_[A-Za-z0-9_]+\b", "pressure instability"),
        (r"\bMAE\b", "pressure difference"),
        (r"\bRMSE\b", "pressure instability"),
        (r"\bPattern\s*[A-F]\b", "pressure behaviour"),
        (r"\b[A-F]\+[A-F](\+[A-F])*\b", "combined pressure behaviour"),
        (r"\bP[1-5]\b", "cause group"),
        (r"\brule\s*ID\b", "rule reference"),
        (r"\bStage\s*1\b|\bS1\b", "first pressurisation phase"),
        (r"\bStage\s*2\b|\bS2\b", "second pressurisation phase"),
        (r"\bStage\s*3\b|\bS3\b", "pressure holding phase"),
        (r"\bS1\s*window\b", "first pressurisation phase"),
        (r"\bS2\s*window\b", "second pressurisation phase"),
        (r"\bS3\s*window\b", "pressure holding phase"),
        (r"\bsub-window\b", "phase period"),
        (r"\bnormalisation\b|\bnormalization\b", "normal comparison"),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Remove broken fragments caused by missing metric numbers.
    broken_patterns = [
        r"\bfrom normal\s+from normal\b",
        r"\bis\s*,\s*which\b",
        r"\bof\s*\.\b",
        r"\bof\s*,\b",
        r"\bnormal is\s*,\b",
        r"\bserious pressure limit of\s*\.\b",
    ]
    for pattern in broken_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    text = re.sub(r"\b0\.\d{3,}\b", "", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" -;,.\t")

    if text and text[-1] not in ".!?":
        text += "."

    return text


def _clean_chatbot_action(value: Any) -> str:
    """Clean a knowledge-base recommendation action for chatbot point form."""
    text = normalize_text(str(value or ""))
    if not text:
        return ""

    # Remove numbering/bullet prefixes.
    text = re.sub(r"^[-•\s]*\d+[.)]\s*", "", text).strip()
    text = re.sub(r"^[-•]\s*", "", text).strip()

    # Remove target/metric endings. Actions should be operational, not scoring targets.
    text = re.split(r"\bTarget\s*:", text, flags=re.IGNORECASE)[0].strip()

    cleaned = _clean_chatbot_sentence(text)
    if not cleaned:
        return ""

    # Avoid returning symptom-only or impact-only sentences as actions.
    lower = cleaned.lower()
    bad_starts = [
        "stage ",
        "first pressurisation phase",
        "second pressurisation phase",
        "pressure holding phase",
        "directly affects",
        "this indicates",
        "this means",
        "the pressure",
    ]
    if any(lower.startswith(start) for start in bad_starts) and not lower.startswith(("check", "inspect", "monitor", "verify", "reduce", "stagger", "compare", "ensure", "ask")):
        return ""

    return cleaned


def _is_weak_cause_text(value: Any) -> bool:
    text = normalize_text(str(value or "")).lower()
    if not text:
        return True

    weak_phrases = [
        "should be checked using the pressure chart",
        "equipment condition",
        "not available",
        "needs further checking",
        "exact cause still needs",
        "likely cause should be checked",
    ]

    if any(phrase in text for phrase in weak_phrases):
        return True

    # Broken generated sentences should not be used in chatbot cause summary.
    if re.search(r"\bis\s*,\s*which\b|\bof\s*\.\b|from normal\s+from normal", text):
        return True

    return len(text) < 35


def _split_sentences(value: Any) -> List[str]:
    text = normalize_text(str(value or ""))
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [part.strip() for part in parts if part.strip()]


def _get_plain_payload(rca_response: Dict[str, Any]) -> Dict[str, Any]:
    feedback = (rca_response or {}).get("feedback") or {}
    human = feedback.get("human_feedback") or {}

    for key in ["plain_language_feedback", "plain_language", "operator_explanation"]:
        payload = human.get(key) or feedback.get(key)
        if isinstance(payload, dict):
            return payload

    return {}


def _section_value(payload: Dict[str, Any], section_key: str) -> Any:
    if not isinstance(payload, dict):
        return None

    if payload.get(section_key):
        return payload.get(section_key)

    camel_keys = {
        "what_happened": "whatHappened",
        "most_likely_cause": "mostLikelyCause",
        "what_to_do": "whatToDo",
    }
    camel_key = camel_keys.get(section_key)
    if camel_key and payload.get(camel_key):
        return payload.get(camel_key)

    sections = payload.get("sections") or []
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            if section.get("key") == section_key:
                return section.get("body") or section.get("items")

    return None


def _extract_peer_count_text(evidence_context: Dict[str, Any]) -> str:
    if not isinstance(evidence_context, dict):
        return ""

    # Try direct peer counts first.
    for key in [
        "active_peer_count",
        "peer_active_count",
        "same_window_peer_count",
        "competing_peer_count",
        "concurrent_peer_count",
    ]:
        value = evidence_context.get(key)
        try:
            number = int(value)
        except Exception:
            continue
        if number > 0:
            if number == 1:
                return "One other sterilizer was also active."
            return f"{number} other sterilizers were also active."

    # Then try summary lines generated by evidence_service.py.
    summary_lines = evidence_context.get("summary_lines") or []
    text = " ".join(str(line) for line in summary_lines if str(line).strip())
    if not text:
        text = str(evidence_context.get("reason") or "")

    if not text:
        return ""

    m = re.search(r"(\d+)\s+(?:other\s+)?sterilizers?", text, flags=re.IGNORECASE)
    if m:
        number = int(m.group(1))
        if number == 1:
            return "One other sterilizer was also active."
        return f"{number} other sterilizers were also active."

    if re.search(r"multiple|several|peer|concurrent|same time|also active", text, flags=re.IGNORECASE):
        return "Other sterilizers were also active."

    return ""


def _root_cause_summary_from_rule(
    primary_rule: Dict[str, Any],
    evidence_context: Dict[str, Any],
    stage: Optional[str],
) -> str:
    """
    Build a brief cause-only summary from the matched RCA rule.

    This is intentionally shorter than the RCA feedback panel. It avoids metric
    values, thresholds, raw rule text, and the full recommendation sentence.
    """
    if not isinstance(primary_rule, dict) or not primary_rule:
        return ""

    attribution = str(primary_rule.get("attribution") or "").strip().lower()
    recommendation = str(primary_rule.get("recommendation_en") or "").strip()
    stage_text = _plain_stage_name(stage or primary_rule.get("stage"))
    peer_text = _extract_peer_count_text(evidence_context)

    if attribution == "boiler":
        if peer_text:
            return _clean_chatbot_sentence(
                f"The most likely cause is a shared steam supply issue linked to the boiler. "
                f"The boiler may not be supplying enough steam during the {stage_text}, especially because {peer_text[0].lower() + peer_text[1:]}"
            )
        return _clean_chatbot_sentence(
            f"The most likely cause is a shared steam supply issue linked to the boiler. "
            f"The boiler may not be supplying enough steam during the {stage_text}."
        )

    if attribution == "competition":
        if peer_text:
            return _clean_chatbot_sentence(
                f"The most likely cause is steam demand competition between sterilizers. "
                f"This can happen when {peer_text[0].lower() + peer_text[1:]}"
            )
        return _clean_chatbot_sentence(
            f"The most likely cause is steam demand competition between sterilizers during the {stage_text}."
        )

    if attribution == "bpv":
        return _clean_chatbot_sentence(
            f"The most likely cause is that the steam pressure control valve may not be regulating steam properly during the {stage_text}."
        )

    if attribution == "network":
        return _clean_chatbot_sentence(
            f"The most likely cause is uneven steam distribution in the main steam line during the {stage_text}."
        )

    if attribution == "local":
        return _clean_chatbot_sentence(
            f"The most likely cause is a local issue on the selected sterilizer during the {stage_text}."
        )

    # Fallback cause summary from the non-action part of the recommendation.
    if recommendation:
        cause_part = re.split(r"\bCheck\b|\bInspect\b|\bVerify\b|\bMonitor\b|\bTarget\b", recommendation, flags=re.IGNORECASE)[0]
        cause_part = cause_part.replace("—", ". ")
        cleaned = _clean_chatbot_sentence(cause_part)
        if cleaned and not _is_weak_cause_text(cleaned):
            return cleaned

    return ""


def _extract_action_phrase_from_recommendation(text: str) -> str:
    """Return only the operational part of a KB recommendation sentence."""
    value = normalize_text(text)
    if not value:
        return ""

    value = re.split(r"\bTarget\s*:", value, flags=re.IGNORECASE)[0].strip()

    # Prefer the first operational verb. This removes symptom/impact wording like
    # "Stage 2 second peak below benchmark — Boiler output dropping...".
    match = re.search(r"\b(Check|Inspect|Verify|Monitor|Reduce|Stagger|Compare|Ensure|Ask)\b.+", value, flags=re.IGNORECASE)
    if match:
        return match.group(0).strip()

    return value


def _split_action_phrase_to_points(action_phrase: str, stage: Optional[str]) -> List[str]:
    """Split a KB action phrase into clean operator bullet points."""
    phrase = normalize_text(action_phrase)
    if not phrase:
        return []

    stage_text = _plain_stage_name(stage)

    # Keep sentence boundaries first.
    sentence_candidates = _split_sentences(phrase)
    if not sentence_candidates:
        sentence_candidates = [phrase]

    output: List[str] = []

    for sentence in sentence_candidates:
        sentence = normalize_text(sentence)
        if not sentence:
            continue

        # Common KB wording:
        # "Check boiler load and steam demand balance during S2 window."
        lower = sentence.lower()
        if re.search(r"check\s+boiler\s+load\s+and\s+steam\s+demand\s+balance", lower):
            output.append("Check boiler load.")
            output.append(f"Check steam demand balance during the {stage_text}.")
            continue

        # Common KB wording:
        # "Check boiler feed water supply, combustion efficiency and steam trap condition."
        if lower.startswith("check boiler feed water supply"):
            output.append("Check boiler feed water supply.")
            if "combustion" in lower:
                output.append("Check boiler combustion efficiency.")
            if "steam trap" in lower:
                output.append("Check steam trap condition.")
            continue

        # Split compact check lists after the first action verb.
        verb_match = re.match(r"^(Check|Inspect|Verify|Monitor|Reduce|Stagger|Compare|Ensure|Ask)\s+(.+)$", sentence, flags=re.IGNORECASE)
        if verb_match:
            verb = verb_match.group(1).capitalize()
            rest = verb_match.group(2).strip().rstrip(".")

            # Do not split when there is a colon explanation, because it is already
            # a good single action point.
            if ":" not in rest:
                rest_for_split = re.sub(r"\s+and\s+", ", ", rest, flags=re.IGNORECASE)
                parts = [p.strip(" ,") for p in rest_for_split.split(",") if p.strip(" ,")]
                if 2 <= len(parts) <= 4:
                    for part in parts:
                        action = f"{verb} {part}."
                        output.append(action)
                    continue

        cleaned = _clean_chatbot_action(sentence)
        if cleaned:
            output.append(cleaned)

    return _unique_clean_list(output)


def extract_recommended_action_points(rca_response: Dict[str, Any]) -> List[str]:
    """
    Extract point-form actions from the same RCA payload used by the RCA panel.

    Priority order:
    1. Sheet 10 / RCA panel `what_to_do` section.
    2. `human_feedback.recommended_actions`.
    3. `feedback.next_actions`.
    4. Matched `primary_rule.recommendation_en` from the knowledge base.

    The chatbot does not invent new actions. It only cleans/parses the returned
    RCA recommendation into point form.
    """
    feedback = (rca_response or {}).get("feedback") or {}
    human = feedback.get("human_feedback") or {}
    primary_rule = feedback.get("primary_rule") or {}
    stage = (
        (human.get("technical_details") or {}).get("selected_stage")
        or primary_rule.get("stage")
        or (rca_response or {}).get("affected_stage")
    )

    payload = _get_plain_payload(rca_response)
    candidates: List[Any] = []

    what_to_do = _section_value(payload, "what_to_do")
    if what_to_do:
        candidates.extend(_as_list(what_to_do))

    recommended = human.get("recommended_actions")
    if recommended:
        candidates.extend(_as_list(recommended))

    next_actions = feedback.get("next_actions")
    if next_actions:
        candidates.extend(_as_list(next_actions))

    recommendation_en = primary_rule.get("recommendation_en")
    if recommendation_en:
        candidates.extend(_as_list(recommendation_en))

    actions: List[str] = []
    for item in candidates:
        phrase = _extract_action_phrase_from_recommendation(str(item))
        points = _split_action_phrase_to_points(phrase, stage)
        actions.extend(points)

    # Remove raw symptom/impact sentences and keep concise action items.
    cleaned = []
    for action in _unique_clean_list(actions):
        lower = action.lower()
        if not action:
            continue
        if "normal operating profile" in lower and not lower.startswith(("check", "inspect", "monitor", "verify", "reduce", "stagger", "compare", "ensure", "ask")):
            continue
        if any(term in lower for term in ["target:", "pressure difference", "measured condition", "metric"]):
            continue
        cleaned.append(action)

    return cleaned[:5]


def extract_most_likely_cause_summary(rca_response: Dict[str, Any]) -> str:
    """
    Extract a brief most likely cause summary for chatbot RCA replies.

    The RCA feedback panel can show a fuller explanation. The chatbot should only
    summarize the cause, so it uses the matched RCA rule/evidence first and only
    falls back to generated text when rule/evidence context is unavailable.
    """
    feedback = (rca_response or {}).get("feedback") or {}
    human = feedback.get("human_feedback") or {}
    primary_rule = feedback.get("primary_rule") or {}
    evidence_context = (rca_response or {}).get("evidence_context") or feedback.get("evidence_context") or {}

    technical_details = human.get("technical_details") or {}
    selected_stage = (
        technical_details.get("selected_stage")
        or primary_rule.get("stage")
        or (rca_response or {}).get("affected_stage")
    )

    # 1) Prefer concise rule/evidence summary to avoid long metric-heavy LLM text.
    rule_summary = _root_cause_summary_from_rule(primary_rule, evidence_context, selected_stage)
    if rule_summary:
        return rule_summary

    payload = _get_plain_payload(rca_response)

    # 2) Use the Sheet 10 cause section only if it is already brief and useful.
    explicit_cause = _clean_chatbot_sentence(_section_value(payload, "most_likely_cause"))
    if explicit_cause and not _is_weak_cause_text(explicit_cause) and len(explicit_cause) <= 320:
        return explicit_cause

    # 3) If the latest RCA UI combines cause into WHAT HAPPENED, extract only
    #    cause-related sentences and keep it brief.
    what_happened = _section_value(payload, "what_happened")
    cause_sentences = []
    for sentence in _split_sentences(" ".join(_as_list(what_happened))):
        lower = sentence.lower()
        if any(
            marker in lower
            for marker in [
                "most likely cause",
                "likely cause",
                "boiler",
                "steam supply",
                "steam demand",
                "other sterilizer",
                "shared steam",
                "multiple sterilizer",
                "supplying enough steam",
            ]
        ):
            cleaned = _clean_chatbot_sentence(sentence)
            if cleaned and not _is_weak_cause_text(cleaned):
                cause_sentences.append(cleaned)

    if cause_sentences:
        return _clean_chatbot_sentence(" ".join(cause_sentences[:2]))

    return "The RCA result was generated, but the most likely cause summary was not available in the returned RCA payload."


def extract_human_feedback_text(rca_response: Dict[str, Any]) -> Tuple[str, str]:
    """
    Backward-compatible wrapper returning chatbot cause summary and actions.
    """
    cause_summary = extract_most_likely_cause_summary(rca_response)
    action_points = extract_recommended_action_points(rca_response)
    action_text = "\n".join(f"- {action}" for action in action_points)
    return cause_summary, action_text

def run_rca_for_chat(
    *,
    cycle: Dict[str, Any],
    context: Dict[str, Any],
    stage: Optional[str],
) -> Dict[str, Any]:
    scoring = enrich_cycle_for_rca(cycle=cycle, context=context, stage=stage)
    return run_rca_feedback_pipeline(
        scoring_result=scoring,
        peer_confirmation_available=False,
    )


def cycle_summary_line(cycle: Dict[str, Any]) -> str:
    cycle_no = cycle.get("cycle_no")
    score = safe_float(cycle.get("score"))
    band = score_band(score)
    status = "Anomaly" if is_anomaly_score(score) else "Normal"
    start = cycle.get("cycle_start") or "-"

    sb = cycle.get("score_breakdown") or {}
    s1 = format_score(sb.get("s1_score")) if "s1_score" in sb else "-"
    s2 = format_score(sb.get("s2_score")) if "s2_score" in sb else "-"
    s3 = format_score(sb.get("s3_score")) if "s3_score" in sb else "-"

    return (
        f"Cycle {cycle_no}: {status} — score {format_score(score)} ({band}), "
        f"S1 {s1}, S2 {s2}, S3 {s3}. Start: {start}."
    )


def build_multi_cycle_reply(cycles: List[Dict[str, Any]], context: Dict[str, Any]) -> str:
    lines = [
        f"I found {len(cycles)} complete cycle(s) in the selected time range.",
        "",
    ]

    for cycle in cycles:
        lines.append(cycle_summary_line(cycle))

    anomaly_cycles = [c for c in cycles if is_anomaly_score(c.get("score"))]
    if anomaly_cycles:
        anomaly_numbers = ", ".join(f"Cycle {c.get('cycle_no')}" for c in anomaly_cycles)
        lines.extend(
            [
                "",
                f"The cycle(s) that need attention are: {anomaly_numbers}.",
                "Please choose one cycle for detailed RCA, for example: 'Explain Cycle 2' or 'Explain Stage 2 of Cycle 2'.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "All detected cycles are within the current normal range based on the selected benchmark.",
            ]
        )

    return "\n".join(lines)


def build_normal_reply(cycle: Dict[str, Any], stage: Optional[str] = None) -> str:
    if stage:
        st_score = get_stage_score_from_cycle(cycle, stage)
        return (
            f"{stage.replace('S', 'Stage ')} of Cycle {cycle.get('cycle_no')} looks normal based on the current threshold. "
            f"Its score is {format_score(st_score)} ({score_band(st_score)}), which means it is close enough to the benchmark. "
            "No RCA recommendation is required unless the operator still sees an unusual pressure pattern on the chart."
        )

    return (
        f"Cycle {cycle.get('cycle_no')} looks normal based on the current threshold. "
        f"Its overall score is {format_score(cycle.get('score'))} ({score_band(cycle.get('score'))}), "
        "so it is close enough to the benchmark. No RCA recommendation is required."
    )


def build_anomaly_reply(
    *,
    cycle: Dict[str, Any],
    context: Dict[str, Any],
    stage: Optional[str],
    rca_response: Dict[str, Any],
) -> str:
    """
    Chatbot RCA reply.

    Desired behaviour:
    - Most likely cause summary is brief, shorter than the RCA feedback panel.
    - Recommended actions are still shown, but only in point form.
    - Action content is extracted from the RCA/KB recommendation payload, not
      invented by the chatbot.
    """
    cause_summary = extract_most_likely_cause_summary(rca_response)
    action_points = extract_recommended_action_points(rca_response)

    score_to_report = get_stage_score_from_cycle(cycle, stage) if stage else cycle.get("score")
    target = f"{stage.replace('S', 'Stage ')} of Cycle {cycle.get('cycle_no')}" if stage else f"Cycle {cycle.get('cycle_no')}"

    lines = [
        f"{target} is abnormal based on the current analysis result.",
        f"Score: {format_score(score_to_report)} ({score_band(score_to_report)}).",
        "",
        "Most likely cause summary:",
        cause_summary,
    ]

    if action_points:
        lines.extend(["", "Recommended actions:"])
        lines.extend(f"- {action}" for action in action_points)
    else:
        lines.extend([
            "",
            "Recommended actions:",
            "- No rule-focused recommended action was returned by the RCA knowledge base for this result.",
        ])

    return "\n".join(line for line in lines if str(line).strip() or line == "")

def build_what_if_reply(message: str, context: Dict[str, Any], parsed: ParsedChatRequest) -> str:
    cycle_no = parsed.cycle_no
    if not cycle_no:
        return (
            "I can answer that, but please tell me which cycle you are referring to, "
            "for example: 'How if I adjust my boiler for Cycle 3?'"
        )

    cycles = context.get("cycle_results") or []
    cycle = get_cycle_by_no(cycles, cycle_no)
    if not cycle:
        return (
            f"I cannot find Cycle {cycle_no} in the current chatbot context. "
            "Please run an analysis with a time range first, then ask about that cycle."
        )

    lower = message.lower()
    if "boiler" in lower:
        return (
            f"Adjusting the boiler may help Cycle {cycle_no} only if the RCA evidence points to a boiler or shared steam supply issue. "
            "The system should not recommend boiler adjustment based only on one abnormal score. "
            "Please check whether the RCA root cause is Boiler (P1), whether multiple sterilizers show related pressure movement, and whether boiler operating data also supports insufficient steam output. "
            "If the RCA is only Candidate, verify the evidence first before changing boiler settings."
        )

    return (
        f"For Cycle {cycle_no}, it is possible to improve the result only after confirming the actual RCA cause. "
        "Use the RCA feedback first, then apply the recommended action from the RCA result."
    )


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------
def handle_chatbot_message(
    *,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    form_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context = compact_context(context or {})
    form_params = form_params or {}
    parsed = parse_chat_message(message)

    # Optional parameter form overrides natural language parsing when provided.
    for key in [
        "bucket",
        "measurement",
        "tag_id",
        "field",
        "source_unit",
        "start_time",
        "stop_time",
        "stage",
        "cycle_no",
        "benchmark_file_name",
    ]:
        if form_params.get(key) not in [None, ""]:
            value = form_params.get(key)
            if key in {"start_time", "stop_time"}:
                value = sanitize_datetime_for_influx(value)
            setattr(parsed, key, value)

    if parsed.intent == "what_if":
        return {
            "reply": build_what_if_reply(message, context, parsed),
            "context": context,
        }

    # Merge with previous context for follow-up questions.
    bucket = parsed.bucket or context.get("bucket") or DEFAULT_BUCKET
    measurement = parsed.measurement or context.get("measurement") or DEFAULT_MEASUREMENT
    tag_id = parsed.tag_id or context.get("tag_id")
    field = parsed.field or context.get("field")
    source_unit = parsed.source_unit or context.get("source_unit")
    start_time = sanitize_datetime_for_influx(parsed.start_time or context.get("start_time"))
    stop_time = sanitize_datetime_for_influx(parsed.stop_time or context.get("stop_time"))
    benchmark_file_name = parsed.benchmark_file_name or context.get("benchmark_file_name")
    stage = parsed.stage
    cycle_no = parsed.cycle_no

    # Follow-up question: user selected a cycle/stage from previous summary.
    if cycle_no and context.get("cycle_results"):
        cycle = get_cycle_by_no(context.get("cycle_results") or [], cycle_no)
        if not cycle:
            return {
                "reply": f"I cannot find Cycle {cycle_no} in the previous analysis. Please choose one of the listed cycle numbers.",
                "context": context,
            }

        if stage:
            st_score = get_stage_score_from_cycle(cycle, stage)
            if not is_anomaly_score(st_score):
                return {"reply": build_normal_reply(cycle, stage), "context": context}
        else:
            if not is_anomaly_score(cycle.get("score")):
                return {"reply": build_normal_reply(cycle), "context": context}

        rca_response = run_rca_for_chat(cycle=cycle, context=context, stage=stage)
        reply = build_anomaly_reply(
            cycle=cycle,
            context=context,
            stage=stage,
            rca_response=rca_response,
        )

        new_context = dict(context)
        new_context["last_selected_cycle_no"] = cycle_no
        new_context["last_selected_stage"] = stage
        new_context["last_rca_most_likely_cause_summary"] = extract_most_likely_cause_summary(rca_response)
        return {"reply": reply, "context": compact_context(new_context)}

    # New analysis requires enough parameters.
    missing = []
    if not tag_id:
        missing.append("sterilizer site/tag, for example SKPG")
    if not field:
        missing.append("sterilizer number/channel, for example Sterilizer 3 or ch4")
    if not start_time or not stop_time:
        missing.append("start and end time")

    if missing:
        return {
            "reply": (
                "I need a bit more information before I can analyse the cycle. Please provide: "
                + ", ".join(missing)
                + ".\n\nExample: Check SKPG Sterilizer 3 from 24 June 2026 1pm to 3pm."
            ),
            "context": context,
        }

    # Load default benchmark to decide source unit if user did not provide it.
    benchmark = find_default_benchmark(
        tag_id=tag_id,
        field=field,
        explicit_name=benchmark_file_name,
    )
    benchmark_file_name = benchmark.get("file_name") or benchmark_file_name
    source_unit = source_unit or benchmark.get("source_unit") or benchmark.get("benchmark_unit") or benchmark.get("unit") or "bar"

    cycles, benchmark = run_cycle_comparison(
        bucket=bucket,
        measurement=measurement,
        tag_id=tag_id,
        field=field,
        start_time=start_time,
        stop_time=stop_time,
        source_unit=source_unit,
        benchmark_file_name=benchmark_file_name,
    )

    new_context = {
        "bucket": bucket,
        "measurement": measurement,
        "tag_id": tag_id,
        "field": field,
        "source_unit": source_unit,
        "start_time": start_time,
        "stop_time": stop_time,
        "benchmark_file_name": benchmark.get("file_name") or benchmark_file_name,
        "benchmark_name": benchmark.get("benchmark_name"),
        "cycle_results": cycles,
    }

    # If user gave a time range and multiple cycles are found, summarize all and ask user to choose one.
    if len(cycles) > 1 and not cycle_no:
        return {
            "reply": build_multi_cycle_reply(cycles, new_context),
            "context": compact_context(new_context),
        }

    # If only one cycle is found, analyse automatically.
    selected_cycle = cycles[0]
    if stage:
        st_score = get_stage_score_from_cycle(selected_cycle, stage)
        if not is_anomaly_score(st_score):
            return {"reply": build_normal_reply(selected_cycle, stage), "context": compact_context(new_context)}
    else:
        if not is_anomaly_score(selected_cycle.get("score")):
            return {"reply": build_normal_reply(selected_cycle), "context": compact_context(new_context)}

    rca_response = run_rca_for_chat(cycle=selected_cycle, context=new_context, stage=stage)
    reply = build_anomaly_reply(
        cycle=selected_cycle,
        context=new_context,
        stage=stage,
        rca_response=rca_response,
    )
    new_context["last_selected_cycle_no"] = selected_cycle.get("cycle_no")
    new_context["last_selected_stage"] = stage
    new_context["last_rca_most_likely_cause_summary"] = extract_most_likely_cause_summary(rca_response)
    return {"reply": reply, "context": compact_context(new_context)}
