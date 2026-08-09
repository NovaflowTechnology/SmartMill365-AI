import numpy as np


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