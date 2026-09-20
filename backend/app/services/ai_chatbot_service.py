"""
AI chatbot service for sterilizer cycle analysis.

Purpose
-------
This service powers a bottom-right chatbot that can answer questions such as:
- "Check SAMYSK_POM_240004 Sterilizer 3 in psi from 24 June 2026 1pm to 3pm."
- "Is Stage 2 of Cycle 2 abnormal?"
- "Analyse stp4 from SAMYSK_POM_240004 between 2026-06-24 13:00 and 15:00."

Design
------
The chatbot is intentionally NOT a free-form diagnosing LLM. It uses the same
existing deterministic cycle comparison + RCA/RAG pipeline as the detail page.
The LLM is used only inside the RCA feedback generator for explanation wording.

Flow
----
1. Parse user natural language for tag/sterilizer/time/stage/cycle.
2. Resolve the active benchmark from Supabase for the selected plant/sterilizer.
3. If a new time range is provided, run comparison and summarize all cycles.
4. If user selects one cycle, check normal/anomaly using current threshold (<75).
5. If anomaly, call the existing RCA pipeline automatically:
   - no stage specified -> cycle-level RCA
   - stage specified -> stage-focused RCA
6. Return a human-friendly text reply plus hidden context for frontend memory.

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

from app.config import (
    CYCLE_QUERY_PADDING_MINUTES,
    PLANT_TIMEZONE,
)
from app.services.influx_service import fetch_data, get_pressure_source
from app.services.signal_service import prepare_signal
from app.services.cycle_service import (
    detect_full_cycles,
    refine_cycle_boundaries,
    restrict_cycles_to_time_range,
)
from app.services.timezone_service import normalise_query_range, pad_query_range
from app.services.benchmark_service import (
    load_benchmark,
    validate_benchmark_compatibility,
)
from app.services.daily_report_settings_service import (
    get_active_benchmark_file_name,
    load_daily_report_settings,
    load_official_plant_names,
    load_plant_custom_aliases,
)
from app.services.sterilizer_mapping import get_all_tags, get_sterilizers_for_tag
from app.services.comparison_service import compare_detected_cycles_to_benchmark
from app.rag.rca_pipeline import run_rca_feedback_pipeline


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
DEFAULT_SMOOTH_WINDOW = int(os.getenv("CHATBOT_DEFAULT_SMOOTH_WINDOW", "9"))
# An omitted unit is intentionally deterministic for the conversational flow.
# Users may still explicitly request psi.
CHATBOT_DEFAULT_UNIT = "bar"
ANOMALY_SCORE_THRESHOLD = float(os.getenv("CHATBOT_ANOMALY_SCORE_THRESHOLD", "75"))

# Important for natural-language dates. Malaysia local time is UTC+8.
# You can change this in .env if needed:
# CHATBOT_LOCAL_TIMEZONE=Asia/Kuala_Lumpur
CHATBOT_LOCAL_TIMEZONE = os.getenv("CHATBOT_LOCAL_TIMEZONE", PLANT_TIMEZONE)
CHATBOT_LOCAL_UTC_OFFSET_HOURS = float(os.getenv("CHATBOT_LOCAL_UTC_OFFSET_HOURS", "8"))


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
    analysis_date: Optional[str] = None
    start_clock: Optional[str] = None
    stop_clock: Optional[str] = None
    stage: Optional[str] = None
    cycle_no: Optional[int] = None
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
            "Check SAMYSK_POM_240004 Sterilizer 3 in bar from 2026-06-24 13:00 to 2026-06-24 15:00."
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

    # Keep direct Data-ID parsing broad enough for other mills while plant-name
    # resolution below remains constrained to the discovered catalogue.
    tag_match = re.search(
        r"\b[A-Z0-9]+(?:_[A-Z0-9]+){2,}\b",
        text,
        flags=re.IGNORECASE,
    )
    field_match = re.search(
        r"\b(?:stp|sterilizer|steriliser)\s*[-_ ]?\s*(\d+)\b",
        text,
        flags=re.IGNORECASE,
    )
    unit_match = re.search(r"\b(bar|psi)\b", text, flags=re.IGNORECASE)

    tag_id = tag_match.group(0).upper() if tag_match else None
    field = f"stp{int(field_match.group(1))}" if field_match else None
    source_unit = unit_match.group(1).lower() if unit_match else None

    return tag_id, field, source_unit


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
    """Parse a two-clock range and return local RFC3339 timestamps.

    Conversational rules:
    - Both ``9:20am`` and ``9.20am`` are accepted.
    - A time-only range (for example ``3am to 9am``) means *today*.
    - If the message contains an explicit date elsewhere, that date is used for
      both clocks (for example ``18 Aug 2026, from 1pm to 3pm``).
    - A stop clock earlier than/equal to the start clock is treated as crossing
      midnight; future-range validation later decides whether that range has
      actually occurred yet.
    """
    text = normalize_text(message)
    text = re.sub(r"\b(?:using|use)\s+benchmark\s+.+$", "", text, flags=re.IGNORECASE).strip()

    # Normalise dot-separated times before dateutil sees them.
    text = re.sub(
        r"\b([01]?\d|2[0-3])\.([0-5]\d)(\s*(?:am|pm))?\b",
        r"\1:\2\3",
        text,
        flags=re.IGNORECASE,
    )

    # Prefer an explicit single date + two clock values before the generic
    # ``from ... to ...`` parser below. This prevents conversational input such
    # as:
    #
    #     from 7 September 2026 to 5pm to 8pm
    #
    # from being misread as 00:00 -> 20:00. In that wording, the first ``to``
    # separates the date from the start clock, not the start datetime from the
    # stop datetime. When exactly one date and two clocks are present, bind both
    # clocks to that date explicitly.
    message_date = _parse_date_slot(text)
    slot_start, slot_stop = _parse_time_slots(text)
    if (
        message_date
        and slot_start
        and slot_stop
        and _count_date_references(text) == 1
    ):
        start_dt = datetime.fromisoformat(f"{message_date}T{slot_start}:00").replace(
            tzinfo=get_chatbot_timezone()
        )
        stop_dt = datetime.fromisoformat(f"{message_date}T{slot_stop}:00").replace(
            tzinfo=get_chatbot_timezone()
        )
        if stop_dt <= start_dt:
            stop_dt += timedelta(days=1)
        return (
            _format_rfc3339_for_influx(start_dt),
            _format_rfc3339_for_influx(stop_dt),
        )

    m = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:$|\?|\.)", text, flags=re.IGNORECASE)
    if not m:
        m = re.search(
            r"\bfrom\s+(.+?)\s+(?:to|until|till)\s+(.+?)(?:$|\?|\.)",
            text,
            flags=re.IGNORECASE,
        )

    if not m:
        # Also accept concise ranges such as "3am to 9am" without "from".
        start_clock, stop_clock = _parse_time_slots(text)
        if not start_clock or not stop_clock:
            return None, None
        analysis_date = _parse_date_slot(text) or datetime.now(
            tz=get_chatbot_timezone()
        ).date().isoformat()
        start_dt = datetime.fromisoformat(f"{analysis_date}T{start_clock}:00").replace(
            tzinfo=get_chatbot_timezone()
        )
        stop_dt = datetime.fromisoformat(f"{analysis_date}T{stop_clock}:00").replace(
            tzinfo=get_chatbot_timezone()
        )
        if stop_dt <= start_dt:
            stop_dt += timedelta(days=1)
        return _format_rfc3339_for_influx(start_dt), _format_rfc3339_for_influx(stop_dt)

    start_raw = _cleanup_datetime_phrase(m.group(1))
    stop_raw = _cleanup_datetime_phrase(m.group(2))

    # "now" is handled by parse_relative_time_range because it must be bound
    # to the exact request-processing time, not parsed as a clock string.
    if re.search(r"\b(?:now|current\s+time)\b", stop_raw, flags=re.IGNORECASE):
        return None, None

    try:
        message_date = _parse_date_slot(text)
        start_has_date = _contains_date_reference(start_raw)
        stop_has_date = _contains_date_reference(stop_raw)

        if not start_has_date and not stop_has_date:
            analysis_date = message_date or datetime.now(
                tz=get_chatbot_timezone()
            ).date().isoformat()
            start_clock = _normalise_clock(start_raw)
            stop_clock = _normalise_clock(stop_raw)
            if not start_clock or not stop_clock:
                return None, None
            start_dt = datetime.fromisoformat(f"{analysis_date}T{start_clock}:00").replace(
                tzinfo=get_chatbot_timezone()
            )
            stop_dt = datetime.fromisoformat(f"{analysis_date}T{stop_clock}:00").replace(
                tzinfo=get_chatbot_timezone()
            )
        else:
            start_dt = _parse_dt(start_raw)
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


MONTH_WORDS = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    "jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", re.IGNORECASE),
    re.compile(
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_WORDS})\s+\d{{4}}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{MONTH_WORDS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s+\d{{4}}\b",
        re.IGNORECASE,
    ),
]
TIME_TOKEN_PATTERN = re.compile(
    # Accept both colon and dot separators because users commonly type
    # "9:20am" and "9.20am" interchangeably in chat.
    r"(?<![\d:.])(?:[01]?\d|2[0-3])[:.][0-5]\d\s*(?:am|pm)?\b|"
    r"(?<![\d:.])(?:1[0-2]|0?[1-9])(?:\s*)?(?:am|pm)\b",
    re.IGNORECASE,
)


def _count_date_references(message: str) -> int:
    """Count distinct explicit date references in one chat message.

    Used to distinguish one-date/two-clock requests (where both clocks should
    inherit the same date) from ranges that explicitly provide a date on both
    sides.
    """
    text = str(message or "")
    spans = set()
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            spans.add((match.start(), match.end()))

    lower = text.lower()
    for relative_word in ("today", "yesterday"):
        for match in re.finditer(rf"\b{relative_word}\b", lower):
            spans.add((match.start(), match.end()))

    return len(spans)


def _contains_date_reference(value: str) -> bool:
    lower = str(value or "").lower()
    if re.search(r"\b(?:today|yesterday)\b", lower):
        return True
    return any(pattern.search(str(value or "")) for pattern in DATE_PATTERNS)


def _parse_date_slot(message: str, now: Optional[datetime] = None) -> Optional[str]:
    """Return a local YYYY-MM-DD date without inventing one from a clock."""

    local_now = _ensure_timezone(now or datetime.now(tz=get_chatbot_timezone()))
    lower = str(message or "").lower()
    if re.search(r"\byesterday\b", lower):
        return (local_now.date() - timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", lower):
        return local_now.date().isoformat()

    for pattern in DATE_PATTERNS:
        match = pattern.search(str(message or ""))
        if not match:
            continue
        try:
            parsed = date_parser.parse(match.group(0), dayfirst=True, fuzzy=True)
            return parsed.date().isoformat()
        except Exception:
            continue
    return None


def _normalise_clock(value: str) -> Optional[str]:
    try:
        # dateutil does not reliably interpret a dot as a time separator.
        # Normalise common chat input such as 9.20am -> 9:20am first.
        raw = str(value or "").strip()
        raw = re.sub(
            r"\b([01]?\d|2[0-3])\.([0-5]\d)(\s*(?:am|pm))?\b",
            r"\1:\2\3",
            raw,
            flags=re.IGNORECASE,
        )
        default = datetime(2000, 1, 1, 0, 0, 0)
        parsed = date_parser.parse(raw, default=default, fuzzy=True)
        return parsed.strftime("%H:%M")
    except Exception:
        return None


def _parse_time_slots(message: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract labelled or ranged start/end clocks from a message."""

    text = normalize_text(message)
    start_label = re.search(
        rf"\bstart(?:\s+time)?\s*(?:is|=|:)?\s*({TIME_TOKEN_PATTERN.pattern})",
        text,
        flags=re.IGNORECASE,
    )
    stop_label = re.search(
        rf"\b(?:end|stop)(?:\s+time)?\s*(?:is|=|:)?\s*({TIME_TOKEN_PATTERN.pattern})",
        text,
        flags=re.IGNORECASE,
    )
    if start_label or stop_label:
        return (
            _normalise_clock(start_label.group(1)) if start_label else None,
            _normalise_clock(stop_label.group(1)) if stop_label else None,
        )

    range_match = re.search(
        rf"\b(?:from|between)\s+({TIME_TOKEN_PATTERN.pattern})\s+"
        rf"(?:to|and)\s+({TIME_TOKEN_PATTERN.pattern})",
        text,
        flags=re.IGNORECASE,
    )
    if range_match:
        return _normalise_clock(range_match.group(1)), _normalise_clock(range_match.group(2))

    tokens = [match.group(0) for match in TIME_TOKEN_PATTERN.finditer(text)]
    if len(tokens) >= 2:
        return _normalise_clock(tokens[0]), _normalise_clock(tokens[1])
    if len(tokens) == 1:
        return _normalise_clock(tokens[0]), None
    return None, None


def parse_relative_time_range(
    message: str,
    now: Optional[datetime] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve relative ranges in the plant timezone.

    Supported examples include ``last 3 hours``, ``today``, ``yesterday`` and
    conversational end points such as ``today 3am onward until now``.  ``now``
    is captured at request-processing time.
    """

    local_now = _ensure_timezone(now or datetime.now(tz=get_chatbot_timezone()))
    lower = normalize_text(message).lower()
    match = re.search(
        r"\blast\s+(\d+)\s*(minute|minutes|hour|hours|day|days)\b",
        lower,
    )
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if "minute" in unit:
            delta = timedelta(minutes=amount)
        elif "hour" in unit:
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        return (
            _format_rfc3339_for_influx(local_now - delta),
            _format_rfc3339_for_influx(local_now),
        )

    # Natural conversational form: "from today 3am onward until now",
    # "3am till now", "from 03:00 to current time", etc.
    if re.search(r"\b(?:now|current\s+time)\b", lower):
        clocks = [match.group(0) for match in TIME_TOKEN_PATTERN.finditer(message)]
        start_clock = _normalise_clock(clocks[0]) if clocks else None
        date_value = _parse_date_slot(message, now=local_now) or local_now.date().isoformat()
        start_value = None
        if start_clock:
            start_dt = datetime.fromisoformat(f"{date_value}T{start_clock}:00").replace(
                tzinfo=get_chatbot_timezone()
            )
            start_value = _format_rfc3339_for_influx(start_dt)
        return start_value, _format_rfc3339_for_influx(local_now)

    # A bare relative day is a complete range. With explicit clock values, the
    # date is kept as a slot and materialised after both clocks are available.
    if re.search(r"\b(?:today|yesterday)\b", lower):
        start_clock, stop_clock = _parse_time_slots(message)
        if not start_clock and not stop_clock:
            date_value = _parse_date_slot(message, now=local_now)
            day_start = datetime.fromisoformat(date_value).replace(tzinfo=get_chatbot_timezone())
            if "today" in lower:
                return (
                    _format_rfc3339_for_influx(day_start),
                    _format_rfc3339_for_influx(local_now),
                )
            return (
                _format_rfc3339_for_influx(day_start),
                _format_rfc3339_for_influx(day_start + timedelta(days=1)),
            )
    return None, None


def _normalise_lookup_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def build_plant_catalog(source_unit: str = "bar") -> List[Dict[str, Any]]:
    """Build a read-only plant catalogue from Influx discovery and Supabase names."""

    tags = get_all_tags(source_unit=source_unit, refresh=False)
    data_ids = [str(item.get("tag_id") or "").strip() for item in tags]
    data_ids = [value for value in data_ids if value]
    official_names = load_official_plant_names(data_ids)
    custom_aliases = load_plant_custom_aliases(data_ids)

    catalog: List[Dict[str, Any]] = []
    for data_id in data_ids:
        official_name = str(official_names.get(data_id) or "").strip()
        custom_alias = str(custom_aliases.get(data_id) or "").strip()
        display_name = custom_alias or official_name or data_id
        aliases = []
        for value in [data_id, custom_alias, official_name, display_name]:
            clean = str(value or "").strip()
            if clean and clean not in aliases:
                aliases.append(clean)
        catalog.append(
            {
                "tag_id": data_id,
                "display_name": display_name,
                "official_name": official_name,
                "custom_display_name": custom_alias,
                "aliases": aliases,
            }
        )
    return sorted(catalog, key=lambda item: (item["display_name"].lower(), item["tag_id"]))


def resolve_plant_reference(
    message: str,
    catalog: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve one clear Data ID/name match; return candidates when ambiguous."""

    raw = str(message or "").strip()
    normalised_message = _normalise_lookup_text(raw)
    if not normalised_message:
        return None, []

    matches: Dict[str, Dict[str, Any]] = {}
    match_scores: Dict[str, Tuple[int, int]] = {}
    for item in catalog:
        for alias in item.get("aliases") or []:
            normalised_alias = _normalise_lookup_text(alias)
            if not normalised_alias:
                continue
            if normalised_alias == normalised_message or re.search(
                rf"(?:^|\s){re.escape(normalised_alias)}(?:$|\s)",
                normalised_message,
            ):
                matches[item["tag_id"]] = item
                score = (len(normalised_alias.split()), len(normalised_alias))
                if score > match_scores.get(item["tag_id"], (0, 0)):
                    match_scores[item["tag_id"]] = score

    if not matches:
        return None, []
    best_score = max(match_scores.values())
    candidates = sorted(
        [item for tag_id, item in matches.items() if match_scores[tag_id] == best_score],
        key=lambda item: (item["display_name"].lower(), item["tag_id"]),
    )
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def format_plant_choices(catalog: List[Dict[str, Any]]) -> str:
    lines = []
    for item in catalog:
        label = item.get("display_name") or item.get("tag_id")
        tag_id = item.get("tag_id")
        lines.append(f"- {label} ({tag_id})" if label != tag_id else f"- {tag_id}")
    return "\n".join(lines)


def format_sterilizer_choices(options: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"- {item.get('sterilizer_name') or item.get('field')} ({item.get('field')})"
        for item in options
    )


def _materialize_time_range(request: Dict[str, Any]) -> None:
    """Combine saved date/clock slots into RFC3339 start and stop values.

    A clock without an explicit date inherits the date already stored in the
    pending conversation.  If there is no prior date, it means today.
    """

    start_time = sanitize_datetime_for_influx(request.get("start_time"))
    stop_time = sanitize_datetime_for_influx(request.get("stop_time"))
    analysis_date = request.get("analysis_date")

    if start_time and not analysis_date:
        try:
            analysis_date = _parse_dt(start_time).date().isoformat()
        except Exception:
            pass
    if stop_time and not analysis_date:
        try:
            analysis_date = _parse_dt(stop_time).date().isoformat()
        except Exception:
            pass

    start_clock = request.get("start_clock")
    stop_clock = request.get("stop_clock")
    if start_time and not start_clock:
        try:
            start_clock = _parse_dt(start_time).strftime("%H:%M")
        except Exception:
            pass
    if stop_time and not stop_clock:
        try:
            stop_clock = _parse_dt(stop_time).strftime("%H:%M")
        except Exception:
            pass

    # Conversation rule: an unqualified clock/range means today unless an
    # earlier turn already supplied a date (which remains in analysis_date).
    if not analysis_date and (start_clock or stop_clock):
        analysis_date = datetime.now(tz=get_chatbot_timezone()).date().isoformat()

    if analysis_date and start_clock and not start_time:
        start_time = _format_rfc3339_for_influx(
            datetime.fromisoformat(f"{analysis_date}T{start_clock}:00").replace(
                tzinfo=get_chatbot_timezone()
            )
        )
    if analysis_date and stop_clock and not stop_time:
        stop_time = _format_rfc3339_for_influx(
            datetime.fromisoformat(f"{analysis_date}T{stop_clock}:00").replace(
                tzinfo=get_chatbot_timezone()
            )
        )

    if start_time and stop_time:
        try:
            start_dt = _parse_dt(start_time)
            stop_dt = _parse_dt(stop_time)
            if stop_dt <= start_dt:
                stop_dt += timedelta(days=1)
                stop_time = _format_rfc3339_for_influx(stop_dt)
        except Exception:
            pass

    request.update(
        {
            "analysis_date": analysis_date,
            "start_clock": start_clock,
            "stop_clock": stop_clock,
            "start_time": start_time,
            "stop_time": stop_time,
        }
    )


MISSING_FIELD_LABELS = {
    "plant": "Plant name or Data ID",
    "sterilizer": "Sterilizer",
    "date": "Date",
    "start_time": "Start time",
    "end_time": "End time",
}


def get_missing_request_fields(request: Dict[str, Any]) -> List[str]:
    _materialize_time_range(request)
    missing = []
    if not request.get("tag_id"):
        missing.append("plant")
    if not request.get("field"):
        missing.append("sterilizer")
    if not request.get("start_time") or not request.get("stop_time"):
        if not request.get("analysis_date"):
            missing.append("date")
        if not request.get("start_time"):
            missing.append("start_time")
        if not request.get("stop_time"):
            missing.append("end_time")
    return missing


def get_future_time_range_error(
    request: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return a user-facing error when the requested range has not occurred yet."""

    _materialize_time_range(request)
    local_now = _ensure_timezone(now or datetime.now(tz=get_chatbot_timezone()))

    future_labels: List[str] = []
    for key, label in (("start_time", "start time"), ("stop_time", "end time")):
        value = request.get(key)
        if not value:
            continue
        try:
            dt = _parse_dt(value)
        except Exception:
            continue
        if dt > local_now:
            future_labels.append(label)

    if not future_labels:
        return None

    labels = " and ".join(future_labels)
    verb = "are" if len(future_labels) > 1 else "is"
    return (
        f"The requested {labels} {verb} in the future. The current plant-local time is "
        f"{local_now.strftime('%d %B %Y %I:%M %p')}. Please provide a time range "
        "that has already occurred (ending at or before the current time)."
    )


def clear_future_time_slots(request: Dict[str, Any]) -> None:
    """Clear an invalid future range while preserving usable conversation slots."""

    local_today = datetime.now(tz=get_chatbot_timezone()).date()
    analysis_date = request.get("analysis_date")

    for key in ("start_time", "stop_time", "start_clock", "stop_clock"):
        request.pop(key, None)

    # Keep today/a past date so the user can reply with only corrected clocks.
    # A future date itself must be requested again.
    if analysis_date:
        try:
            if datetime.fromisoformat(str(analysis_date)).date() > local_today:
                request.pop("analysis_date", None)
        except Exception:
            request.pop("analysis_date", None)


def build_missing_fields_reply(missing: List[str]) -> str:
    lines = [f"- {MISSING_FIELD_LABELS[item]}" for item in missing]
    return (
        "I saved the information you provided. Before I can run the analysis, "
        "I still need:\n"
        + "\n".join(lines)
        + "\n\nYou may provide all of them together or only some now. "
        "For example: P1 - Sterilizer, Sterilizer 3, 18 August 2026, from 1pm to 3pm. "
        "If no unit is specified, I will use bar."
    )


def build_pending_context(
    previous_context: Dict[str, Any],
    request: Dict[str, Any],
    *,
    missing_fields: Optional[List[str]] = None,
    awaiting_active_benchmark: bool = False,
    plant_choices: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Store a compact in-progress request for the next chatbot message."""

    context = dict(previous_context or {})
    clean_request = {
        key: value
        for key, value in request.items()
        if value not in [None, ""]
    }
    context["pending_request"] = clean_request
    context["missing_fields"] = list(missing_fields or [])
    context["awaiting_clarification"] = bool(missing_fields)
    context["awaiting_active_benchmark"] = bool(awaiting_active_benchmark)
    context["plant_choices"] = [
        {
            "tag_id": item.get("tag_id"),
            "display_name": item.get("display_name") or item.get("tag_id"),
        }
        for item in (plant_choices or [])
    ]
    for key in [
        "tag_id",
        "plant_display_name",
        "field",
        "source_unit",
        "start_time",
        "stop_time",
    ]:
        if clean_request.get(key) not in [None, ""]:
            context[key] = clean_request[key]
    return compact_context(context)


def detect_intent(message: str) -> str:
    lower = message.lower()
    if any(word in lower for word in ["adjust", "solve", "possible", "what if", "how if", "improve"]):
        return "what_if"
    return "analyze"


def parse_chat_message(message: str) -> ParsedChatRequest:
    tag_id, field, unit = parse_tag_and_field(message)

    # Relative parsing may return only one side (for example "until now").
    # Merge it with the normal range parser instead of replacing it.
    relative_start, relative_stop = parse_relative_time_range(message)
    range_start, range_stop = parse_time_range(message)
    start_time = relative_start or range_start
    stop_time = relative_stop or range_stop

    analysis_date = _parse_date_slot(message)
    start_clock, stop_clock = _parse_time_slots(message)

    return ParsedChatRequest(
        bucket=None,
        measurement=None,
        tag_id=tag_id,
        field=field,
        source_unit=unit,
        start_time=start_time,
        stop_time=stop_time,
        analysis_date=analysis_date,
        start_clock=start_clock,
        stop_clock=stop_clock,
        stage=parse_stage(message),
        cycle_no=parse_cycle_no(message),
        intent=detect_intent(message),
    )


# -----------------------------------------------------------------------------
# Benchmark and comparison
# -----------------------------------------------------------------------------
class ActiveBenchmarkUnavailableError(ValueError):
    """No usable active benchmark is configured for the requested sterilizer."""


def load_active_benchmark(
    *,
    tag_id: str,
    field: str,
) -> Dict[str, Any]:
    """Load the benchmark assigned as active for one plant/sterilizer.

    The chatbot intentionally has no manual benchmark override.  The active
    assignment in Supabase is the single source of truth for every new chatbot
    analysis.  This keeps chatbot scoring consistent with AI Comparison and
    Daily Report.
    """

    clean_tag_id = str(tag_id or "").strip()
    clean_field = str(field or "").strip().lower()
    if not clean_tag_id or not clean_field:
        raise ActiveBenchmarkUnavailableError(
            "Plant Data ID and sterilizer are required before an active benchmark can be resolved."
        )

    # This loads the validated operator-managed settings from Supabase.
    # SettingsStorageError is deliberately not swallowed here: if Supabase is
    # unavailable/misconfigured, the API should report the real service failure
    # instead of pretending that no active benchmark was configured.
    settings = load_daily_report_settings()
    file_name = get_active_benchmark_file_name(settings, clean_tag_id, clean_field)
    if not file_name:
        sterilizer_no = re.sub(r"^stp", "", clean_field, flags=re.IGNORECASE)
        sterilizer_label = f"Sterilizer {sterilizer_no}" if sterilizer_no.isdigit() else clean_field
        raise ActiveBenchmarkUnavailableError(
            f"No active benchmark is configured for {clean_tag_id} {sterilizer_label}. "
            "Please configure an active benchmark in Settings before running cycle analysis."
        )

    try:
        benchmark = load_benchmark(file_name)
        validate_benchmark_compatibility(
            benchmark,
            tag_id=clean_tag_id,
            field=clean_field,
            file_name=file_name,
        )
    except FileNotFoundError as exc:
        raise ActiveBenchmarkUnavailableError(
            f"The active benchmark configured for {clean_tag_id} {clean_field} could not be loaded. "
            "Please reassign the active benchmark in Settings."
        ) from exc
    except ValueError as exc:
        raise ActiveBenchmarkUnavailableError(
            f"The active benchmark configured for {clean_tag_id} {clean_field} is not compatible with this sterilizer. "
            "Please correct the active benchmark assignment in Settings."
        ) from exc

    benchmark["file_name"] = file_name
    benchmark["_selection_source"] = "active_benchmark_setting"
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
    benchmark: Dict[str, Any],
    smooth_window: int = DEFAULT_SMOOTH_WINDOW,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    start_time = sanitize_datetime_for_influx(start_time)
    stop_time = sanitize_datetime_for_influx(stop_time)

    if not start_time or not stop_time:
        raise ValueError("Start time and stop time are required for chatbot analysis.")

    start_time, stop_time = normalise_query_range(start_time, stop_time)
    query_start, query_stop = pad_query_range(
        start_time,
        stop_time,
        CYCLE_QUERY_PADDING_MINUTES,
    )

    source = get_pressure_source(source_unit, requested_bucket=bucket)

    benchmark_file_name = str((benchmark or {}).get("file_name") or "").strip()
    if not benchmark_file_name:
        raise ValueError("An active benchmark must be resolved before chatbot comparison.")

    # Use exactly the active benchmark object resolved at the start of this NEW
    # chatbot analysis.  Do not search for or substitute another benchmark.
    validate_benchmark_compatibility(
        benchmark,
        tag_id=tag_id,
        field=field,
        file_name=benchmark_file_name,
    )

    df = fetch_data(
        bucket=source["bucket"],
        measurement=source["measurement"],
        field=field,
        tag_id=tag_id,
        start_time=query_start,
        stop_time=query_stop,
        source_unit_fallback=source["source_unit"],
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
    )
    df, cycles_idx = restrict_cycles_to_time_range(
        df,
        cycles_idx,
        start_time,
        stop_time,
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
        "S1": "first pressurization phase",
        "S2": "second pressurization phase",
        "S3": "pressure holding phase",
    }.get(str(stage or "").upper(), "the selected cycle period")


def _clean_chatbot_sentence(value: Any) -> str:
    """Clean wording for a short chatbot RCA cause/action sentence."""
    text = normalize_text(str(value or ""))
    if not text:
        return ""

    replacements = [
        (r"\bRCA\b", "analysis"),
        (r"\bbenchmark\b", "normal operating profile"),
        (r"\bthreshold\b", "limit"),
        (r"\bdeviation\b", "difference from normal"),
        (r"\bMAE_[A-Za-z0-9_]+\b", "pressure difference"),
        (r"\bRMSE_[A-Za-z0-9_]+\b", "pressure instability"),
        (r"\bMAE\b", "pressure difference"),
        (r"\bRMSE\b", "pressure instability"),
        (r"\bPattern\s*[A-F]\b", "pressure behavior"),
        (r"\b[A-F]\+[A-F](\+[A-F])*\b", "combined pressure behavior"),
        (r"\bP[1-5]\b", "cause group"),
        (r"\brule\s*ID\b", "rule reference"),
        (r"\bStage\s*1\b|\bS1\b", "first pressurization phase"),
        (r"\bStage\s*2\b|\bS2\b", "second pressurization phase"),
        (r"\bStage\s*3\b|\bS3\b", "pressure holding phase"),
        (r"\bS1\s*window\b", "first pressurization phase"),
        (r"\bS2\s*window\b", "second pressurization phase"),
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
        "first pressurization phase",
        "second pressurization phase",
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

    return "The analysis result was generated, but the most likely cause summary was not available in the returned payload."


def _get_rca_evidence_context(rca_response: Dict[str, Any]) -> Dict[str, Any]:
    """Return the engineering evidence regardless of where the pipeline stored it."""
    response = rca_response or {}
    feedback = response.get("feedback") or {}
    human = feedback.get("human_feedback") or {}
    technical = human.get("technical_details") or {}
    operator_facts = human.get("operator_facts") or {}
    primary_rule = feedback.get("primary_rule") or {}

    for candidate in [
        response.get("evidence_context"),
        feedback.get("evidence_context"),
        technical.get("evidence_context"),
        operator_facts.get("evidence_context"),
        primary_rule.get("evidence_context"),
    ]:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _chatbot_root_cause(rca_response: Dict[str, Any]) -> str:
    """Return the matched root-cause attribution in a consistent lowercase form."""
    feedback = (rca_response or {}).get("feedback") or {}
    human = feedback.get("human_feedback") or {}
    technical = human.get("technical_details") or {}
    operator_facts = human.get("operator_facts") or {}
    primary_rule = feedback.get("primary_rule") or {}
    payload = _get_plain_payload(rca_response)

    value = (
        primary_rule.get("attribution")
        or technical.get("attribution")
        or operator_facts.get("attribution")
        or payload.get("root_cause")
        or payload.get("rootCause")
        or ""
    )
    return str(value).strip().lower()


def _format_chatbot_evidence_time(value: Any) -> str:
    """Display evidence timestamps in Malaysia's operator-friendly format."""
    if not value:
        return "time unavailable"
    try:
        parsed = date_parser.parse(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=get_chatbot_timezone())
        else:
            parsed = parsed.astimezone(get_chatbot_timezone())
        return parsed.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(value)


def _format_chatbot_pressure(value: Any) -> str:
    number = safe_float(value)
    return f"{number:.2f}" if number is not None else "-"


def _raw_or_standard_pressure(stats: Dict[str, Any], name: str) -> Any:
    return stats.get(f"raw_{name}_pressure", stats.get(f"{name}_pressure"))


def _pressure_trend_for_chat(stats: Dict[str, Any]) -> str:
    """Describe the start-to-end direction without adding a diagnostic claim."""
    start = safe_float(_raw_or_standard_pressure(stats, "start"))
    end = safe_float(_raw_or_standard_pressure(stats, "end"))
    minimum = safe_float(_raw_or_standard_pressure(stats, "min"))
    maximum = safe_float(_raw_or_standard_pressure(stats, "max"))
    if start is None or end is None:
        return "pressure trend unavailable"

    span = max((maximum if maximum is not None else start) - (minimum if minimum is not None else start), 0.0)
    tolerance = max(span * 0.08, 0.05)
    if end - start > tolerance:
        return "pressure rising"
    if end - start < -tolerance:
        return "pressure falling"
    return "pressure relatively stable"


def _pressure_record_line(
    *,
    name: str,
    field: Optional[str],
    stats: Dict[str, Any],
    condition: Optional[str] = None,
) -> str:
    unit = stats.get("source_unit") or stats.get("benchmark_unit") or "pressure units"
    field_text = f" ({field})" if field else ""
    condition_text = str(condition or _pressure_trend_for_chat(stats)).strip().rstrip(".")
    return (
        f"- {name}{field_text}: {condition_text}; "
        f"minimum {_format_chatbot_pressure(_raw_or_standard_pressure(stats, 'min'))} {unit}, "
        f"mean {_format_chatbot_pressure(_raw_or_standard_pressure(stats, 'mean'))} {unit}, "
        f"maximum {_format_chatbot_pressure(_raw_or_standard_pressure(stats, 'max'))} {unit}, "
        f"start {_format_chatbot_pressure(_raw_or_standard_pressure(stats, 'start'))} {unit}, "
        f"end {_format_chatbot_pressure(_raw_or_standard_pressure(stats, 'end'))} {unit}."
    )


def _selected_sterilizer_name(context: Dict[str, Any]) -> str:
    field = str((context or {}).get("field") or "")
    match = re.fullmatch(r"stp(\d+)", field, flags=re.IGNORECASE)
    if match:
        return f"Sterilizer {int(match.group(1))}"
    return "Selected sterilizer"


def extract_chatbot_pressure_evidence_lines(
    rca_response: Dict[str, Any],
    context: Dict[str, Any],
) -> List[str]:
    """
    Build compact, one-equipment-per-line pressure evidence for the chatbot.

    Sterilizer evidence is always shown when available. Boiler or BPV evidence
    is added only when that equipment matches the rule's root-cause attribution.
    This keeps the reply relevant and prevents unrelated equipment readings from
    being presented as support for the diagnosis.
    """
    evidence = _get_rca_evidence_context(rca_response)
    if not evidence:
        return []

    root_cause = _chatbot_root_cause(rca_response)
    selected_item = evidence.get("selected_pressure_time_evidence") or {}
    selected_stats = selected_item.get("stage_window_stats") or {}
    peer_evidence = evidence.get("peer_sterilizer_evidence") or {}
    peer_conditions = peer_evidence.get("affected_stage_pressure_conditions") or []
    auxiliary = evidence.get("auxiliary_pressure_evidence") or {}

    window_stats: Dict[str, Any] = {}
    if selected_stats.get("window_start") or selected_stats.get("window_end"):
        window_stats = selected_stats
    else:
        for item in peer_conditions:
            if isinstance(item, dict) and (item.get("window_start") or item.get("window_end")):
                window_stats = item
                break
    if not window_stats and root_cause in {"boiler", "bpv"}:
        window_stats = (auxiliary.get(root_cause) or {}).get("stage_window_stats") or {}

    lines = ["Pressure evidence for the affected stage:"]
    if window_stats:
        lines.append(
            "Time range: "
            f"{_format_chatbot_evidence_time(window_stats.get('window_start'))} to "
            f"{_format_chatbot_evidence_time(window_stats.get('window_end'))}."
        )

    selected_name = _selected_sterilizer_name(context)
    selected_field = str((context or {}).get("field") or selected_item.get("field") or "").strip()
    lines.append("Affected sterilizer:")
    if selected_stats.get("available"):
        lines.append(
            _pressure_record_line(
                name=selected_name,
                field=selected_field or None,
                stats=selected_stats,
            )
        )
    else:
        reason = selected_stats.get("reason") or "no readings were returned for the affected-stage window"
        field_text = f" ({selected_field})" if selected_field else ""
        lines.append(f"- {selected_name}{field_text}: pressure data unavailable because {reason}.")

    if root_cause in {"boiler", "bpv"}:
        default_name = "Boiler" if root_cause == "boiler" else "BPV"
        item = auxiliary.get(root_cause) or {}
        stats = item.get("stage_window_stats") or {}
        equipment_name = str(item.get("display_name") or default_name).strip()
        equipment_field = str(item.get("field") or "").strip()
        lines.append("Root-cause equipment:")
        if item.get("available") and stats.get("available"):
            lines.append(
                _pressure_record_line(
                    name=equipment_name,
                    field=equipment_field or None,
                    stats=stats,
                )
            )
        else:
            reason = item.get("reason") or stats.get("reason")
            if not item:
                reason = f"no {equipment_name} pressure channel is configured for the selected tag"
            elif not reason:
                reason = "no readings were returned for the affected-stage window"
            field_text = f" ({equipment_field})" if equipment_field else ""
            lines.append(f"- {equipment_name}{field_text}: pressure data unavailable because {reason}.")

    available_peers = [
        item for item in peer_conditions
        if isinstance(item, dict) and item.get("available")
    ]
    lines.append("Other sterilizers:")
    if available_peers:
        for item in available_peers:
            lines.append(
                _pressure_record_line(
                    name=str(item.get("sterilizer_name") or item.get("display_name") or "Peer sterilizer"),
                    field=str(item.get("field") or "").strip() or None,
                    stats=item,
                    condition=item.get("pressure_condition"),
                )
            )
    else:
        lines.append("- No relevant peer-sterilizer pressure readings were available for this time range.")

    return lines


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
        f"I found {len(cycles)} complete cycle(s) in the selected time range using the active benchmark configured in Settings.",
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
                "Please choose one cycle for detailed analysis, for example: 'Explain Cycle 2' or 'Explain Stage 2 of Cycle 2'.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "All detected cycles are within the current normal range based on the active benchmark.",
            ]
        )

    return "\n".join(lines)


def build_normal_reply(cycle: Dict[str, Any], stage: Optional[str] = None) -> str:
    if stage:
        st_score = get_stage_score_from_cycle(cycle, stage)
        return (
            f"{stage.replace('S', 'Stage ')} of Cycle {cycle.get('cycle_no')} looks normal based on the current threshold. "
            f"Its score is {format_score(st_score)} ({score_band(st_score)}), which means it is close enough to the benchmark. "
            "No analysis recommendation is required unless the operator still sees an unusual pressure pattern on the chart."
        )

    return (
        f"Cycle {cycle.get('cycle_no')} looks normal based on the current threshold. "
        f"Its overall score is {format_score(cycle.get('score'))} ({score_band(cycle.get('score'))}), "
        "so it is close enough to the benchmark. No analysis recommendation is required."
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
    - Cause-specific pressure evidence is shown one equipment per line.
    - Recommended actions are still shown, but only in point form.
    - Action content is extracted from the RCA/KB recommendation payload, not
      invented by the chatbot.
    """
    cause_summary = extract_most_likely_cause_summary(rca_response)
    pressure_evidence_lines = extract_chatbot_pressure_evidence_lines(
        rca_response,
        context,
    )
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

    if pressure_evidence_lines:
        lines.extend(["", *pressure_evidence_lines])

    if action_points:
        lines.extend(["", "Recommended actions:"])
        lines.extend(f"- {action}" for action in action_points)
    else:
        lines.extend([
            "",
            "Recommended actions:",
            "- No rule-focused recommended action was returned by the analysis knowledge base for this result.",
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
            f"Adjusting the boiler may help Cycle {cycle_no} only if the analysis evidence points to a boiler or shared steam supply issue. "
            "The system should not recommend boiler adjustment based only on one abnormal score. "
            "Please check whether the most likely cause is Boiler, whether multiple sterilizers show related pressure movement, and whether boiler operating data also supports insufficient steam output. "
            "If the analysis finding is only a candidate, verify the evidence first before changing boiler settings."
        )

    return (
        f"For Cycle {cycle_no}, it is possible to improve the result only after confirming the actual cause. "
        "Use the analysis feedback first, then apply the recommended action from the analysis result."
    )


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------
def handle_chatbot_message(
    *,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context = compact_context(context or {})
    parsed = parse_chat_message(message)
    existing_pending = dict(context.get("pending_request") or {})

    if parsed.intent == "what_if" and not existing_pending:
        return {
            "reply": build_what_if_reply(message, context, parsed),
            "context": context,
        }

    stage = parsed.stage
    cycle_no = parsed.cycle_no

    # Follow-up question: user selected a cycle/stage from previous summary.
    has_new_analysis_parameters = any(
        [
            parsed.tag_id,
            parsed.field,
            parsed.start_time,
            parsed.stop_time,
            parsed.analysis_date,
        ]
    )
    if (
        cycle_no
        and context.get("cycle_results")
        and not existing_pending
        and not has_new_analysis_parameters
    ):
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

    # A new analysis starts with a fresh request.  Only an existing pending
    # request is merged, so a later "check..." command cannot silently inherit
    # an old plant or time range from completed results.
    request: Dict[str, Any] = dict(existing_pending)
    source_unit = (
        parsed.source_unit
        or request.get("source_unit")
        or CHATBOT_DEFAULT_UNIT
    )
    request["source_unit"] = source_unit
    if parsed.bucket:
        request["bucket"] = parsed.bucket
    if parsed.measurement:
        request["measurement"] = parsed.measurement
    if parsed.field:
        request["field"] = parsed.field
    if parsed.stage:
        request["stage"] = parsed.stage
    if parsed.cycle_no:
        request["cycle_no"] = parsed.cycle_no

    if parsed.start_time:
        request["start_time"] = sanitize_datetime_for_influx(parsed.start_time)
    if parsed.stop_time:
        request["stop_time"] = sanitize_datetime_for_influx(parsed.stop_time)
    if parsed.analysis_date:
        request["analysis_date"] = parsed.analysis_date

    # A single unlabelled clock fills the first still-missing clock. Labelled
    # start/end values and two-clock ranges retain their explicit positions.
    incoming_start_clock = parsed.start_clock
    incoming_stop_clock = parsed.stop_clock
    if incoming_start_clock and not incoming_stop_clock and existing_pending:
        pending_missing = get_missing_request_fields(dict(request))
        if "start_time" not in pending_missing and "end_time" in pending_missing:
            incoming_stop_clock = incoming_start_clock
            incoming_start_clock = None
    if incoming_start_clock:
        request["start_clock"] = incoming_start_clock
        if not parsed.start_time:
            request.pop("start_time", None)
    if incoming_stop_clock:
        request["stop_clock"] = incoming_stop_clock
        if not parsed.stop_time:
            request.pop("stop_time", None)

    # Resolve either a Data ID or a Supabase-backed official/custom plant name.
    catalog = build_plant_catalog(source_unit=source_unit)
    plant_match, plant_candidates = resolve_plant_reference(message, catalog)
    explicit_plant_supplied = bool(parsed.tag_id)
    if explicit_plant_supplied:
        explicit_match, explicit_candidates = resolve_plant_reference(parsed.tag_id, catalog)
        plant_match = explicit_match
        plant_candidates = explicit_candidates

    if len(plant_candidates) > 1:
        missing = get_missing_request_fields(request)
        pending_context = build_pending_context(
            context,
            request,
            missing_fields=missing,
            plant_choices=plant_candidates,
        )
        return {
            "reply": (
                "That plant name matches more than one plant. Please reply with one "
                "exact plant name or Data ID:\n"
                + format_plant_choices(plant_candidates)
            ),
            "context": pending_context,
        }

    if plant_match:
        previous_tag_id = request.get("tag_id")
        request["tag_id"] = plant_match["tag_id"]
        request["plant_display_name"] = plant_match["display_name"]
        if (
            previous_tag_id
            and previous_tag_id != plant_match["tag_id"]
            and not parsed.field
        ):
            # A sterilizer selection belongs to one plant and must be checked
            # again when the user changes the plant.
            request.pop("field", None)
    elif explicit_plant_supplied:
        request.pop("tag_id", None)
        missing = get_missing_request_fields(request)
        pending_context = build_pending_context(
            context,
            request,
            missing_fields=missing,
            plant_choices=catalog,
        )
        return {
            "reply": (
                f"I could not find '{parsed.tag_id}' in the available plant catalogue. "
                "Please choose one of these plants:\n"
                + format_plant_choices(catalog)
            ),
            "context": pending_context,
        }

    # During clarification, a reply containing only a number is accepted as a
    # sterilizer number (for example, "3" means stp3).
    if not parsed.field and existing_pending and not request.get("field"):
        number_reply = re.fullmatch(r"\s*(\d+)\s*", str(message or ""))
        if number_reply:
            request["field"] = f"stp{int(number_reply.group(1))}"

    sterilizer_options: List[Dict[str, Any]] = []
    if request.get("tag_id"):
        sterilizer_options = get_sterilizers_for_tag(
            request["tag_id"],
            source_unit=source_unit,
            refresh=False,
        )
        valid_fields = {str(item.get("field") or "").lower() for item in sterilizer_options}
        requested_field = str(request.get("field") or "").lower()
        if requested_field and requested_field not in valid_fields:
            invalid_field = request.pop("field", None)
            missing = get_missing_request_fields(request)
            pending_context = build_pending_context(
                context,
                request,
                missing_fields=missing,
            )
            return {
                "reply": (
                    f"{invalid_field} is not an available sterilizer for "
                    f"{request.get('plant_display_name') or request['tag_id']}. "
                    "Please choose one of these valid sterilizers:\n"
                    + (format_sterilizer_choices(sterilizer_options) or "- None discovered")
                ),
                "context": pending_context,
            }

    _materialize_time_range(request)

    future_time_error = get_future_time_range_error(request)
    if future_time_error:
        clear_future_time_slots(request)
        missing = get_missing_request_fields(request)
        return {
            "reply": future_time_error + "\n\n" + build_missing_fields_reply(missing),
            "context": build_pending_context(
                context,
                request,
                missing_fields=missing,
            ),
        }

    missing = get_missing_request_fields(request)

    # If the chatbot was already waiting for a plant and a plain-text reply did
    # not supply any other slot, treat it as an unknown plant name and show the
    # current dynamic choices. Partial replies such as only a date remain valid.
    supplied_nonplant_slot = any(
        [
            parsed.field,
            parsed.start_time,
            parsed.stop_time,
            parsed.analysis_date,
            parsed.start_clock,
            parsed.stop_clock,
        ]
    )
    acknowledgement = bool(
        re.fullmatch(r"\s*(?:done|ready|saved|configured|continue|retry)\s*[.!]?\s*", message, re.IGNORECASE)
    )
    if (
        existing_pending
        and "plant" in missing
        and not plant_match
        and not supplied_nonplant_slot
        and not acknowledgement
    ):
        pending_context = build_pending_context(
            context,
            request,
            missing_fields=missing,
            plant_choices=catalog,
        )
        return {
            "reply": (
                f"I could not match '{normalize_text(message)}' to one available plant. "
                "Please reply with an exact plant name or Data ID:\n"
                + format_plant_choices(catalog)
            ),
            "context": pending_context,
        }

    if missing:
        reply = build_missing_fields_reply(missing)
        if "plant" in missing and catalog:
            reply += "\n\nAvailable plants (official/custom name and Data ID):\n" + format_plant_choices(catalog)
        if request.get("tag_id") and "sterilizer" in missing and sterilizer_options:
            reply += "\n\nValid sterilizers for this plant:\n" + format_sterilizer_choices(sterilizer_options)
        return {
            "reply": reply,
            "context": build_pending_context(
                context,
                request,
                missing_fields=missing,
            ),
        }

    tag_id = request["tag_id"]
    field = request["field"]
    start_time = request["start_time"]
    stop_time = request["stop_time"]
    requested_bucket = request.get("bucket")
    stage = request.get("stage")
    cycle_no = request.get("cycle_no")
    source = get_pressure_source(source_unit, requested_bucket=requested_bucket)
    bucket = source["bucket"]
    measurement = source["measurement"]
    source_unit = source["source_unit"]

    # Every NEW analysis resolves the currently active benchmark from Supabase.
    # There is intentionally no manual or "best matching" fallback.
    # Follow-up questions above do not re-resolve this value; they keep using
    # the already-scored cycle context (Option A behaviour).
    try:
        benchmark = load_active_benchmark(tag_id=tag_id, field=field)
    except ActiveBenchmarkUnavailableError as exc:
        pending_context = build_pending_context(
            context,
            request,
            awaiting_active_benchmark=True,
        )
        return {
            "reply": (
                f"{exc}\n\nYour analysis request is saved. Open Settings and assign the "
                "active benchmark for this plant and sterilizer. Then return to "
                "the chatbot and send 'Done'; I will re-check Supabase and run the "
                "analysis automatically."
            ),
            "context": pending_context,
        }

    benchmark_file_name = str(benchmark.get("file_name") or "").strip()
    source = get_pressure_source(source_unit, requested_bucket=bucket)
    bucket = source["bucket"]
    measurement = source["measurement"]
    source_unit = source["source_unit"]

    cycles, benchmark = run_cycle_comparison(
        bucket=bucket,
        measurement=measurement,
        tag_id=tag_id,
        field=field,
        start_time=start_time,
        stop_time=stop_time,
        source_unit=source_unit,
        benchmark=benchmark,
    )

    new_context = {
        "bucket": bucket,
        "measurement": measurement,
        "tag_id": tag_id,
        "plant_display_name": request.get("plant_display_name") or tag_id,
        "field": field,
        "source_unit": source_unit,
        "start_time": start_time,
        "stop_time": stop_time,
        "benchmark_file_name": benchmark.get("file_name") or benchmark_file_name,
        "benchmark_name": benchmark.get("benchmark_name"),
        "benchmark_id": benchmark.get("benchmark_id") or benchmark.get("database_id"),
        "benchmark_source": "active_benchmark_setting",
        "cycle_results": cycles,
    }

    # If user gave a time range and multiple cycles are found, summarize all and ask user to choose one.
    if len(cycles) > 1 and not cycle_no:
        return {
            "reply": build_multi_cycle_reply(cycles, new_context),
            "context": compact_context(new_context),
        }

    # If one cycle was requested as part of the new analysis, select it from
    # the newly detected results instead of accidentally analysing Cycle 1.
    selected_cycle = get_cycle_by_no(cycles, int(cycle_no)) if cycle_no else cycles[0]
    if selected_cycle is None:
        available = ", ".join(
            str(item.get("cycle_no")) for item in cycles if item.get("cycle_no") is not None
        )
        return {
            "reply": (
                f"Cycle {cycle_no} was not found in this analysis range. "
                f"Available cycle numbers: {available or 'none'}."
            ),
            "context": compact_context(new_context),
        }
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
