import os
import re
import time
from functools import lru_cache
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient
import pandas as pd

from app.config import (
    BENCHMARK_UNIT,
    INFLUX_BUCKET,
    INFLUX_METADATA_CACHE_SECONDS,
    INFLUX_METADATA_LOOKBACK,
    PLANT_TIMEZONE,
)
from app.services.unit_service import (
    convert_values,
    measurement_for_unit,
    normalize_unit_name,
    unit_from_measurement,
)

load_dotenv()

INFLUX_URL = os.getenv("INFLUX_URL")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_TIMEOUT_MS = int(os.getenv("INFLUX_TIMEOUT_MS", "120000"))

_PRESSURE_CATALOG_CACHE = {}


def _validate_influx_env():
    missing = [
        name
        for name, value in {
            "INFLUX_URL": INFLUX_URL,
            "INFLUX_TOKEN": INFLUX_TOKEN,
            "INFLUX_ORG": INFLUX_ORG,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"Missing InfluxDB environment variable(s): {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_influx_client():
    """Reuse one InfluxDB client instead of creating a new connection for every query."""
    _validate_influx_env()
    return InfluxDBClient(
        url=INFLUX_URL,
        token=INFLUX_TOKEN,
        org=INFLUX_ORG,
        timeout=INFLUX_TIMEOUT_MS,
    )


def check_influx_connectivity():
    """Contact InfluxDB and return a sanitized readiness result."""

    try:
        response = get_influx_client().health()
        status = str(getattr(response, "status", "") or "").lower()
        ok = status in {"pass", "ok"}
        return {
            "ok": ok,
            "detail": None if ok else "InfluxDB health check did not pass.",
        }
    except Exception:
        return {"ok": False, "detail": "InfluxDB is unavailable."}


def _escape_flux_string(value):
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


def resolve_bucket(requested_bucket=None):
    """Use the server-configured new bucket; accept request value only for legacy setups."""
    configured = str(INFLUX_BUCKET or os.getenv("INFLUX_BUCKET") or "").strip()
    if configured:
        return configured
    fallback = str(requested_bucket or "").strip()
    if fallback:
        return fallback
    raise ValueError("INFLUX_BUCKET is missing from the backend environment.")


def get_pressure_source(source_unit, requested_bucket=None):
    clean_unit = normalize_unit_name(source_unit)
    return {
        "bucket": resolve_bucket(requested_bucket),
        "measurement": measurement_for_unit(clean_unit),
        "source_unit": clean_unit,
        "benchmark_unit": BENCHMARK_UNIT,
    }


def _normalise_and_convert_df(df, source_unit_fallback=None):
    if df.empty:
        return df

    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(PLANT_TIMEZONE)
    df = df.sort_values(["field", "time"]).reset_index(drop=True)

    if "unit" not in df.columns:
        df["unit"] = None

    df["unit"] = df["unit"].astype(str).str.strip()
    df.loc[df["unit"].isin(["", "None", "nan", "NaN"]), "unit"] = None

    converted_parts = []

    for field_name, field_df in df.groupby("field", sort=False):
        field_df = field_df.copy()
        units = field_df["unit"].dropna().unique().tolist()

        if len(units) > 1:
            raise ValueError(f"Mixed units found for {field_name}: {units}")

        measurement_units = []
        if "measurement" in field_df.columns:
            measurement_units = list(
                dict.fromkeys(
                    inferred
                    for inferred in (
                        unit_from_measurement(value)
                        for value in field_df["measurement"].dropna().unique().tolist()
                    )
                    if inferred
                )
            )

        if len(measurement_units) > 1:
            raise ValueError(
                f"Multiple pressure measurements were returned for {field_name}: {measurement_units}"
            )

        stored_unit = normalize_unit_name(units[0]) if len(units) == 1 else None
        measurement_unit = measurement_units[0] if len(measurement_units) == 1 else None

        if stored_unit and measurement_unit and stored_unit != measurement_unit:
            raise ValueError(
                f"Unit metadata for {field_name} conflicts with its measurement: "
                f"{stored_unit} versus {measurement_unit}."
            )

        if stored_unit:
            source_unit = stored_unit
            unit_source = "unit_tag"
        elif measurement_unit:
            source_unit = measurement_unit
            unit_source = "measurement_name"
        elif source_unit_fallback:
            source_unit = normalize_unit_name(source_unit_fallback)
            unit_source = "request_fallback"
        else:
            raise ValueError("Pressure unit could not be inferred from the measurement name.")

        field_df["value_std"] = convert_values(
            field_df["value"].to_numpy(), source_unit, BENCHMARK_UNIT
        )
        field_df["source_unit"] = source_unit
        field_df["unit_source"] = unit_source
        field_df["benchmark_unit"] = BENCHMARK_UNIT
        converted_parts.append(field_df)

    return pd.concat(converted_parts, ignore_index=True)


def _records_to_dataframe(tables):
    rows = []
    for table in tables:
        for record in table.records:
            rows.append(
                {
                    "time": record.get_time(),
                    "value": float(record.get_value()),
                    "field": record.get_field(),
                    "measurement": record.get_measurement(),
                    "id": record.values.get("id"),
                    "unit": record.values.get("unit"),
                }
            )
    return pd.DataFrame(rows)


def fetch_data(
    bucket,
    measurement,
    field,
    tag_id,
    start_time,
    stop_time,
    source_unit_fallback=None,
    aggregate_every=None,
):
    """
    Fetch one field/channel from InfluxDB.

    Used by benchmark generation and AI comparison. For live monitoring with
    multiple sterilizers, use fetch_multi_field_data() to reduce database round trips.
    """
    bucket = resolve_bucket(bucket)
    client = get_influx_client()
    query_api = client.query_api()

    aggregate_part = ""
    if aggregate_every:
        aggregate_part = f"""
      |> aggregateWindow(every: {aggregate_every}, fn: mean, createEmpty: false)
        """

    query = f'''
    from(bucket: "{_escape_flux_string(bucket)}")
      |> range(start: time(v: "{_escape_flux_string(start_time)}"), stop: time(v: "{_escape_flux_string(stop_time)}"))
      |> filter(fn: (r) =>
          r["_measurement"] == "{_escape_flux_string(measurement)}" and
          r["_field"] == "{_escape_flux_string(field)}" and
          r["id"] == "{_escape_flux_string(tag_id)}"
      )
      {aggregate_part}
      |> sort(columns: ["_time"])
    '''

    tables = query_api.query(org=INFLUX_ORG, query=query)
    df = _records_to_dataframe(tables)
    return _normalise_and_convert_df(df, source_unit_fallback=source_unit_fallback)


def fetch_multi_field_data(
    bucket,
    measurement,
    fields,
    tag_id,
    start_time,
    stop_time,
    source_unit_fallback=None,
    aggregate_every=None,
):
    """
    Fetch multiple channels in one InfluxDB query, then split by field in Python.
    This is much faster for Live Monitoring than making one query per sterilizer.
    """
    bucket = resolve_bucket(bucket)
    clean_fields = [str(f).strip() for f in fields if str(f).strip()]
    if not clean_fields:
        return pd.DataFrame()

    client = get_influx_client()
    query_api = client.query_api()

    fields_array = "[" + ", ".join(f'"{_escape_flux_string(f)}"' for f in clean_fields) + "]"

    aggregate_part = ""
    if aggregate_every:
        aggregate_part = f"""
      |> aggregateWindow(every: {aggregate_every}, fn: mean, createEmpty: false)
        """

    query = f'''
    fields = {fields_array}

    from(bucket: "{_escape_flux_string(bucket)}")
      |> range(start: time(v: "{_escape_flux_string(start_time)}"), stop: time(v: "{_escape_flux_string(stop_time)}"))
      |> filter(fn: (r) =>
          r["_measurement"] == "{_escape_flux_string(measurement)}" and
          contains(value: r["_field"], set: fields) and
          r["id"] == "{_escape_flux_string(tag_id)}"
      )
      {aggregate_part}
      |> sort(columns: ["_field", "_time"])
    '''

    tables = query_api.query(org=INFLUX_ORG, query=query)
    df = _records_to_dataframe(tables)
    return _normalise_and_convert_df(df, source_unit_fallback=source_unit_fallback)


def _safe_flux_lookback(value):
    text = str(value or "").strip()
    if re.fullmatch(r"-\d+[smhdwy]", text):
        return text
    return "-365d"


def clear_pressure_catalog_cache():
    _PRESSURE_CATALOG_CACHE.clear()


def fetch_pressure_field_catalog(source_unit="bar", bucket=None, refresh=False):
    """Discover the currently available (id, _field) pairs for a pressure unit."""
    source = get_pressure_source(source_unit, requested_bucket=bucket)
    cache_key = (source["bucket"], source["measurement"])
    now = time.monotonic()
    cached = _PRESSURE_CATALOG_CACHE.get(cache_key)
    if (
        not refresh
        and cached
        and now - cached["created_at"] < max(INFLUX_METADATA_CACHE_SECONDS, 0)
    ):
        return [dict(item) for item in cached["items"]]

    lookback = _safe_flux_lookback(INFLUX_METADATA_LOOKBACK)
    query = f'''\n    from(bucket: "{_escape_flux_string(source["bucket"])}")
      |> range(start: {lookback})
      |> filter(fn: (r) =>
          r["_measurement"] == "{_escape_flux_string(source["measurement"])}" and
          exists r["id"]
      )
      |> group(columns: ["id", "_field"])
      |> last()
      |> keep(columns: ["id", "_field"])
      |> group()
      |> sort(columns: ["id", "_field"])
    '''

    tables = get_influx_client().query_api().query(org=INFLUX_ORG, query=query)
    seen = set()
    items = []
    for table in tables:
        for record in table.records:
            tag_id = str(record.values.get("id") or "").strip()
            field = str(record.get_field() or record.values.get("_field") or "").strip()
            key = (tag_id, field)
            if not tag_id or not field or key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "tag_id": tag_id,
                    "field": field,
                    "measurement": source["measurement"],
                    "source_unit": source["source_unit"],
                }
            )

    items.sort(key=lambda item: (item["tag_id"], item["field"]))
    _PRESSURE_CATALOG_CACHE[cache_key] = {
        "created_at": now,
        "items": [dict(item) for item in items],
    }
    return items
