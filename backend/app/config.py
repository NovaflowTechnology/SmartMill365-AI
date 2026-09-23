import os

from dotenv import load_dotenv


load_dotenv()


# Signal processing and stored benchmarks use one canonical unit. The selected
# operator unit only chooses the Influx measurement and display conversion.
BENCHMARK_UNIT = "bar"

PRESSURE_MEASUREMENTS = {
    "bar": os.getenv("INFLUX_PRESSURE_MEASUREMENT_BAR", "").strip(),
    "psi": os.getenv("INFLUX_PRESSURE_MEASUREMENT_PSI", "").strip(),
}

DEFAULT_PRESSURE_UNIT = os.getenv("DEFAULT_PRESSURE_UNIT", "bar").strip().lower()
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "").strip()
INFLUX_METADATA_LOOKBACK = os.getenv("INFLUX_METADATA_LOOKBACK", "-365d").strip()
INFLUX_METADATA_CACHE_SECONDS = int(os.getenv("INFLUX_METADATA_CACHE_SECONDS", "300"))

# All user-entered plant dates and times are interpreted in this timezone.
# Keeping the value in one setting makes a future per-site lookup possible
# without changing API contracts or browser behaviour.
PLANT_TIMEZONE = os.getenv("PLANT_TIMEZONE", "Asia/Kuala_Lumpur").strip() or "Asia/Kuala_Lumpur"

# Cycle-boundary confirmation is time-based so detection remains consistent
# when InfluxDB aggregation changes the number of samples per minute.
CYCLE_STABLE_BASELINE_MINUTES = max(
    0.5,
    float(os.getenv("CYCLE_STABLE_BASELINE_MINUTES", "2")),
)
# The detector works internally in canonical bar. Real idle readings can sit
# slightly above zero, so this configurable allowance is applied above the
# observed idle centre when confirming start/end baseline windows.
CYCLE_IDLE_TOLERANCE_BAR = max(
    0.0,
    float(os.getenv("CYCLE_IDLE_TOLERANCE_BAR", "0.03")),
)
CYCLE_BOUNDARY_LOOKAROUND_MINUTES = max(
    CYCLE_STABLE_BASELINE_MINUTES,
    float(os.getenv("CYCLE_BOUNDARY_LOOKAROUND_MINUTES", "20")),
)
CYCLE_QUERY_PADDING_MINUTES = max(
    CYCLE_STABLE_BASELINE_MINUTES,
    float(os.getenv("CYCLE_QUERY_PADDING_MINUTES", "5")),
)
# Daily Report queries include a short period before the operational day so a
# cycle that starts before the boundary but ends inside the day can be detected
# in full. These context records are never counted as an additional report day.
DAILY_REPORT_CYCLE_LOOKBACK_MINUTES = max(
    0.0,
    float(os.getenv("DAILY_REPORT_CYCLE_LOOKBACK_MINUTES", "120")),
)
CYCLE_MAX_DATA_GAP_SECONDS = max(
    1.0,
    float(os.getenv("CYCLE_MAX_DATA_GAP_SECONDS", "60")),
)
CYCLE_MAX_INACTIVE_GAP_MINUTES = max(
    1.0,
    float(os.getenv("CYCLE_MAX_INACTIVE_GAP_MINUTES", "15")),
)
CYCLE_MIN_ACTIVE_MINUTES = max(
    1.0,
    float(os.getenv("CYCLE_MIN_ACTIVE_MINUTES", "5")),
)

# Supabase is the persistent storage for benchmark curves, active benchmark
# assignments, site/plant/shift settings, Analysis rules/knowledge chunks, and
# append-only audit history. Qdrant remains the vector search index.
# Only the backend may use the secret key.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)
SUPABASE_TIMEOUT_SECONDS = float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "20"))
