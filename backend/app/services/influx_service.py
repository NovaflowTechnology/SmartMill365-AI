import os
from functools import lru_cache
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient
import pandas as pd

from app.config import BENCHMARK_UNIT
from app.services.unit_service import convert_values, normalize_unit_name

load_dotenv()

INFLUX_URL = os.getenv("INFLUX_URL")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_TIMEOUT_MS = int(os.getenv("INFLUX_TIMEOUT_MS", "10000"))


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


def _escape_flux_string(value):
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


def _normalise_and_convert_df(df, source_unit_fallback=None):
    if df.empty:
        return df

    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kuala_Lumpur")
    df = df.sort_values(["field", "time"]).reset_index(drop=True)

    df["unit"] = df["unit"].astype(str).str.strip()
    df.loc[df["unit"].isin(["", "None", "nan", "NaN"]), "unit"] = None

    converted_parts = []

    for field_name, field_df in df.groupby("field", sort=False):
        field_df = field_df.copy()
        units = field_df["unit"].dropna().unique().tolist()

        if len(units) > 1:
            raise ValueError(f"Mixed units found for {field_name}: {units}")

        if len(units) == 1:
            source_unit = normalize_unit_name(units[0])
        else:
            if not source_unit_fallback:
                raise ValueError(
                    "No unit found in data. Please choose the source unit manually in the UI."
                )
            source_unit = normalize_unit_name(source_unit_fallback)

        field_df["value_std"] = convert_values(
            field_df["value"].to_numpy(), source_unit, BENCHMARK_UNIT
        )
        field_df["source_unit"] = source_unit
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
