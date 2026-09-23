import numpy as np

from app.config import PRESSURE_MEASUREMENTS

def normalize_unit_name(unit: str) -> str:
    if unit is None:
        raise ValueError("Unit is missing.")

    u = unit.strip().lower()

    if u in ["bar", "bars"]:
        return "bar"
    if u in ["psi"]:
        return "psi"

    raise ValueError(f"Unsupported unit: {unit}")


def convert_values(values, from_unit: str, to_unit: str):
    from_unit = normalize_unit_name(from_unit)
    to_unit = normalize_unit_name(to_unit)

    values = np.asarray(values, dtype=float)

    if from_unit == to_unit:
        return values

    if from_unit == "psi" and to_unit == "bar":
        return values * 0.0689476

    if from_unit == "bar" and to_unit == "psi":
        return values * 14.5038

    raise ValueError(f"Unsupported conversion: {from_unit} -> {to_unit}")


def measurement_for_unit(unit: str) -> str:
    """Return the pressure measurement selected by an operator-facing unit."""
    clean_unit = normalize_unit_name(unit)
    measurement = str(PRESSURE_MEASUREMENTS.get(clean_unit) or "").strip()
    if not measurement:
        raise ValueError(f"No InfluxDB pressure measurement is configured for {clean_unit}.")
    return measurement


def unit_from_measurement(measurement: str):
    """Infer bar/psi from a configured pressure measurement name."""
    clean_measurement = str(measurement or "").strip().lower()
    for unit, configured in PRESSURE_MEASUREMENTS.items():
        if clean_measurement == str(configured or "").strip().lower():
            return unit
    return None


def validate_measurement_matches_unit(measurement: str, unit: str) -> str:
    """Reject stale clients that send a measurement conflicting with unit."""
    expected = measurement_for_unit(unit)
    supplied = str(measurement or "").strip()
    if supplied and supplied.lower() != expected.lower():
        raise ValueError(
            f"Pressure unit {normalize_unit_name(unit)} must use measurement {expected}; "
            f"received {supplied}."
        )
    return expected
