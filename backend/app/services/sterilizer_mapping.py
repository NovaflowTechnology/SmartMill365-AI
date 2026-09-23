"""Discover sterilizers and supporting equipment from InfluxDB metadata.

The new schema stores mill/data identity in the ``id`` tag and equipment in
``_field``. No ID, mill name, sterilizer count, or channel number is hardcoded.
Until the company confirms what ``blr`` represents, it is reported but ignored.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from app.services.influx_service import fetch_pressure_field_catalog
from app.services.unit_service import measurement_for_unit, normalize_unit_name


STERILIZER_FIELD_PATTERN = re.compile(r"^stp(\d+)$", re.IGNORECASE)
SUPPORTED_AUXILIARY_FIELDS = {"boiler": "Boiler", "bpv": "BPV"}
AMBIGUOUS_AUXILIARY_FIELDS = {"blr"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _field_sort_key(field: str):
    match = STERILIZER_FIELD_PATTERN.fullmatch(_clean(field))
    if match:
        return (0, int(match.group(1)), _clean(field).lower())
    return (1, 0, _clean(field).lower())


def sterilizer_name_from_field(field: str):
    match = STERILIZER_FIELD_PATTERN.fullmatch(_clean(field))
    if not match:
        return None
    return f"Sterilizer {int(match.group(1))}"


def is_sterilizer_field(field: str) -> bool:
    return bool(STERILIZER_FIELD_PATTERN.fullmatch(_clean(field)))


def build_dynamic_tag_catalog(
    catalog_rows: Iterable[Dict[str, Any]], source_unit: str
) -> List[Dict[str, Any]]:
    clean_unit = normalize_unit_name(source_unit)
    measurement = measurement_for_unit(clean_unit)
    grouped: Dict[str, Dict[str, Any]] = {}

    for row in catalog_rows:
        tag_id = _clean(row.get("tag_id") or row.get("id"))
        field = _clean(row.get("field") or row.get("_field"))
        if not tag_id or not field:
            continue

        item = grouped.setdefault(
            tag_id,
            {
                "tag_id": tag_id,
                "tag_name": tag_id,
                "unit": clean_unit,
                "measurement": measurement,
                "sterilizers": [],
                "auxiliary_channels": {},
                "ignored_auxiliary_fields": [],
                "other_fields": [],
            },
        )

        field_key = field.lower()
        sterilizer_name = sterilizer_name_from_field(field)
        if sterilizer_name:
            if not any(x.get("field") == field for x in item["sterilizers"]):
                item["sterilizers"].append(
                    {"field": field, "sterilizer_name": sterilizer_name}
                )
        elif field_key in SUPPORTED_AUXILIARY_FIELDS:
            item["auxiliary_channels"][field_key] = {
                "field": field,
                "display_name": SUPPORTED_AUXILIARY_FIELDS[field_key],
            }
        elif field_key in AMBIGUOUS_AUXILIARY_FIELDS:
            if field not in item["ignored_auxiliary_fields"]:
                item["ignored_auxiliary_fields"].append(field)
        elif field not in item["other_fields"]:
            item["other_fields"].append(field)

    results = []
    for tag_id in sorted(grouped):
        item = grouped[tag_id]
        item["sterilizers"].sort(key=lambda x: _field_sort_key(x["field"]))
        item["ignored_auxiliary_fields"].sort(key=str.lower)
        item["other_fields"].sort(key=str.lower)
        item["sterilizer_count"] = len(item["sterilizers"])
        if item["sterilizer_count"] > 0:
            results.append(item)
    return results


def _load_catalog(source_unit="bar", refresh=False):
    clean_unit = normalize_unit_name(source_unit)
    rows = fetch_pressure_field_catalog(clean_unit, refresh=refresh)
    return build_dynamic_tag_catalog(rows, clean_unit)


def get_all_tags(source_unit="bar", refresh=False):
    return [
        {
            "tag_id": item["tag_id"],
            "tag_name": item["tag_name"],
            "unit": item["unit"],
            "measurement": item["measurement"],
            "sterilizer_count": item["sterilizer_count"],
            "ignored_auxiliary_fields": list(item["ignored_auxiliary_fields"]),
        }
        for item in _load_catalog(source_unit=source_unit, refresh=refresh)
    ]


def get_tag_metadata(tag_id: str, source_unit="bar", refresh=False):
    target = _clean(tag_id)
    for item in _load_catalog(source_unit=source_unit, refresh=refresh):
        if item["tag_id"] == target:
            return item
    return None


def get_sterilizers_for_tag(tag_id: str, source_unit="bar", refresh=False):
    item = get_tag_metadata(tag_id, source_unit=source_unit, refresh=refresh)
    return list(item.get("sterilizers") or []) if item else []


def get_default_unit_for_tag(tag_id: str, source_unit="bar"):
    return normalize_unit_name(source_unit)


def get_auxiliary_channels_for_tag(tag_id: str, source_unit="bar", refresh=False):
    item = get_tag_metadata(tag_id, source_unit=source_unit, refresh=refresh)
    return dict(item.get("auxiliary_channels") or {}) if item else {}


def get_auxiliary_channel_for_root_cause(
    tag_id: str, root_cause: str, source_unit="bar"
):
    key = _clean(root_cause).lower()
    return get_auxiliary_channels_for_tag(tag_id, source_unit=source_unit).get(key)
