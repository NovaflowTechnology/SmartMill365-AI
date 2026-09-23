"""Central plant-time parsing and conversion helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import PLANT_TIMEZONE


def get_plant_timezone(timezone_name: str | None = None) -> ZoneInfo:
    name = str(timezone_name or PLANT_TIMEZONE).strip() or PLANT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Configured plant timezone is invalid: {name}.") from exc


def parse_plant_datetime(
    value: str,
    timezone_name: str | None = None,
) -> datetime:
    """Parse an API datetime, treating a value without an offset as plant time."""

    text = str(value or "").strip()
    if not text:
        raise ValueError("Start and end date/time are required.")
    normalised = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError("Invalid date or time format.") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=get_plant_timezone(timezone_name))
    return parsed


def normalise_query_range(
    start_time: str,
    stop_time: str,
    timezone_name: str | None = None,
) -> Tuple[str, str]:
    """Return a validated InfluxDB range as explicit UTC ISO timestamps."""

    start = parse_plant_datetime(start_time, timezone_name)
    stop = parse_plant_datetime(stop_time, timezone_name)
    if start >= stop:
        raise ValueError("Stop Time must be later than Start Time.")

    return (
        start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        stop.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def pad_query_range(
    start_time: str,
    stop_time: str,
    padding_minutes: float,
) -> Tuple[str, str]:
    """Expand an already validated query range without changing its timezone."""

    start = parse_plant_datetime(start_time).astimezone(timezone.utc)
    stop = parse_plant_datetime(stop_time).astimezone(timezone.utc)
    padding = timedelta(minutes=max(0.0, float(padding_minutes)))
    return (
        (start - padding).isoformat().replace("+00:00", "Z"),
        (stop + padding).isoformat().replace("+00:00", "Z"),
    )
