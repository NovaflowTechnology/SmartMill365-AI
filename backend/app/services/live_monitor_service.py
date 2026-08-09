from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd


PSI_PER_BAR = 14.5038


def get_live_window_iso(
    start_time=None,
    stop_time=None,
    last_n_hours: int = 3,
    tz_name: str = "Asia/Kuala_Lumpur",
):
    """
    Returns the time range used for live monitoring.

    If custom start_time and stop_time are provided, use them.
    Otherwise, use a moving recent time window from now - last_n_hours to now.
    """
    if start_time and stop_time:
        return start_time, stop_time

    stop_dt = datetime.now(ZoneInfo(tz_name)).replace(microsecond=0)
    start_dt = stop_dt - timedelta(hours=last_n_hours)

    return start_dt.isoformat(), stop_dt.isoformat()


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
