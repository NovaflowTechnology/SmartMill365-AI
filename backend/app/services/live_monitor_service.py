from datetime import datetime, timedelta, timezone
import pandas as pd

from app.config import PLANT_TIMEZONE
from app.services.timezone_service import (
    get_plant_timezone,
    normalise_query_range,
    parse_plant_datetime,
)


PSI_PER_BAR = 14.5038


def parse_live_datetime(value, tz_name: str = PLANT_TIMEZONE):
    """Parse a live-monitoring datetime and make its timezone explicit."""
    try:
        return parse_plant_datetime(value, tz_name)
    except ValueError as exc:
        raise ValueError("Invalid live monitoring date or time format.") from exc


def get_live_window_iso(
    start_time=None,
    stop_time=None,
    last_n_hours: int = 3,
    tz_name: str = PLANT_TIMEZONE,
):
    """
    Returns the time range used for live monitoring.

    If custom start_time and stop_time are provided, validate and use them.
    Otherwise, use a moving recent time window from now - last_n_hours to now.
    """
    has_start = bool(str(start_time or "").strip())
    has_stop = bool(str(stop_time or "").strip())

    if has_start != has_stop:
        raise ValueError(
            "Both start date/time and end date/time are required for a custom range."
        )

    if has_start and has_stop:
        return normalise_query_range(start_time, stop_time, tz_name)

    if int(last_n_hours or 0) <= 0:
        raise ValueError("The latest monitoring window must be greater than 0 hours.")

    stop_dt = datetime.now(get_plant_timezone(tz_name)).replace(microsecond=0)
    start_dt = stop_dt - timedelta(hours=last_n_hours)

    return (
        start_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        stop_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def get_live_window_hours(start_time, stop_time, tz_name: str = PLANT_TIMEZONE):
    """Return the validated live-monitoring window duration in hours."""
    start_dt = parse_live_datetime(start_time, tz_name=tz_name)
    stop_dt = parse_live_datetime(stop_time, tz_name=tz_name)
    duration_hours = (stop_dt - start_dt).total_seconds() / 3600

    if duration_hours <= 0:
        raise ValueError("End date/time must be later than start date/time.")

    return duration_hours


def get_live_aggregate_window(hours: float) -> str:
    """
    Choose a display resolution that preserves a sterilizer cycle's ramp,
    holding plateau, and release while keeping long requests manageable.

    A one-week request previously used 30-minute means. That left only a few
    points per cycle, so the chart connected those points as artificial
    triangles and cycle detection operated on heavily distorted data.

    These limits keep a typical request near or below about 10,080 points per
    sterilizer (one point per minute for seven days).
    """
    duration_hours = float(hours or 0)
    if duration_hours <= 0:
        raise ValueError("Live monitoring duration must be greater than 0 hours.")
    if duration_hours <= 24:
        return "30s"
    if duration_hours <= 168:
        return "1m"
    if duration_hours <= 336:
        return "2m"
    if duration_hours <= 744:
        return "5m"
    return "10m"


def convert_bar_to_display(value, display_unit="bar"):
    if value is None or pd.isna(value):
        return None

    unit = str(display_unit or "bar").strip().lower()
    if unit == "psi":
        return float(value) * PSI_PER_BAR
    return float(value)


def build_live_sterilizer_payload(
    df,
    cycles_idx,
    sterilizer_name,
    field,
    display_unit="bar",
):
    """
    Build payload for one live-monitoring card.

    The live chart currently displays raw pressure only. The backend still uses
    the smoothed column internally for cycle detection, but does not send it to
    the frontend to reduce JSON size and visual clutter.
    """
    if df is None or df.empty:
        return {
            "sterilizer_name": sterilizer_name,
            "field": field,
            "latest_value": None,
            "status": "no_data",
            "points": [],
            "cycles": [],
            "display_unit": display_unit,
        }

    points = []
    for _, row in df.iterrows():
        raw_std = row.get("value_std")
        points.append(
            {
                "time": (
                    row["time"].isoformat()
                    if hasattr(row["time"], "isoformat")
                    else str(row["time"])
                ),
                "raw": convert_bar_to_display(raw_std, display_unit),
            }
        )

    cycles = []
    for idx, (start_idx, end_idx) in enumerate(cycles_idx, start=1):
        if start_idx < len(df) and end_idx < len(df):
            start_time = df.iloc[start_idx]["time"]
            end_time = df.iloc[end_idx]["time"]

            start_iso = start_time.isoformat() if hasattr(start_time, "isoformat") else str(start_time)
            end_iso = end_time.isoformat() if hasattr(end_time, "isoformat") else str(end_time)

            cycles.append(
                {
                    "cycle_no": idx,
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "start": start_iso,
                    "end": end_iso,
                    "duration_seconds": float((end_time - start_time).total_seconds()),
                }
            )

    latest_value = points[-1]["raw"] if points else None
    status = "idle"

    if len(cycles_idx) > 0:
        _, last_end = cycles_idx[-1]
        if last_end >= len(df) - 2:
            status = "active"
        else:
            status = "recent_cycle_detected"

    return {
        "sterilizer_name": sterilizer_name,
        "field": field,
        "latest_value": latest_value,
        "status": status,
        "points": points,
        "cycles": cycles,
        "display_unit": display_unit,
    }
