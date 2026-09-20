"""UI-managed configuration for the Daily Report module.

Hierarchy: site prefix -> plant Data ID -> sterilizer field.

For example, ``SAMYSK_POM_240004`` is a plant Data ID and its site prefix is
``SAMYSK_POM``. Operators configure friendly names, site shifts, and active
benchmarks from the Settings page; source-code or JSON editing is unnecessary.
"""

from __future__ import annotations

import logging
import os
import re
from copy import deepcopy
from typing import Any, Dict, Optional

from app.error_handling import SettingsConflictError, SettingsStorageError
from app.services.audit_service import try_record_audit_event
from app.services.benchmark_service import (
    load_benchmark,
    validate_benchmark_compatibility,
)
from app.services.supabase_service import (
    SupabaseRestError,
    get_supabase_client,
    postgres_in_filter,
)
from app.config import PLANT_TIMEZONE
from app.services.timezone_service import get_plant_timezone

TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SETTINGS_METADATA_KEY = "daily_report"

DEFAULT_SHIFT_SETTINGS: Dict[str, Any] = {
    "morning": {"label": "Morning Shift", "start": "00:00", "end": "12:00"},
    "night": {"label": "Night Shift", "start": "12:00", "end": "00:00"},
}

DEFAULT_DAILY_REPORT_SETTINGS: Dict[str, Any] = {
    "version": 0,
    "timezone": PLANT_TIMEZONE,
    "sites": [],
    "active_benchmarks": {},
}

LOGGER = logging.getLogger("sterilizer_api")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def derive_site_code(data_id: str) -> str:
    """Derive the site prefix by removing the final plant-specific segment."""

    clean_data_id = _clean(data_id)
    if "_" not in clean_data_id:
        return clean_data_id
    site_code, plant_suffix = clean_data_id.rsplit("_", 1)
    return site_code if site_code and plant_suffix else clean_data_id


def _normalise_time(value: Any, field_name: str) -> str:
    text = _clean(value)
    if not TIME_PATTERN.fullmatch(text):
        raise ValueError(f"{field_name} must use HH:MM in 24-hour format.")
    return text


def _time_to_minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def _validate_shifts(raw_shifts: Dict[str, Any], site_code: str) -> Dict[str, Any]:
    raw_shifts = raw_shifts or deepcopy(DEFAULT_SHIFT_SETTINGS)
    morning = raw_shifts.get("morning") or {}
    night = raw_shifts.get("night") or {}
    prefix = f"Site {site_code}: " if site_code else ""
    clean_shifts = {
        "morning": {
            "label": _clean(morning.get("label")) or "Morning Shift",
            "start": _normalise_time(morning.get("start"), f"{prefix}Morning Shift start"),
            "end": _normalise_time(morning.get("end"), f"{prefix}Morning Shift end"),
        },
        "night": {
            "label": _clean(night.get("label")) or "Night Shift",
            "start": _normalise_time(night.get("start"), f"{prefix}Night Shift start"),
            "end": _normalise_time(night.get("end"), f"{prefix}Night Shift end"),
        },
    }
    if clean_shifts["morning"]["start"] == clean_shifts["morning"]["end"]:
        raise ValueError(f"{prefix}Morning Shift start and end cannot be the same.")
    if clean_shifts["night"]["start"] == clean_shifts["night"]["end"]:
        raise ValueError(f"{prefix}Night Shift start and end cannot be the same.")
    if clean_shifts["morning"]["end"] != clean_shifts["night"]["start"]:
        raise ValueError(f"{prefix}Morning Shift end must match Night Shift start.")
    if clean_shifts["night"]["end"] != clean_shifts["morning"]["start"]:
        raise ValueError(f"{prefix}Night Shift end must match Morning Shift start.")

    morning_start = _time_to_minutes(clean_shifts["morning"]["start"])
    morning_end = _time_to_minutes(clean_shifts["morning"]["end"])
    night_start = _time_to_minutes(clean_shifts["night"]["start"])
    night_end = _time_to_minutes(clean_shifts["night"]["end"])
    if ((morning_end - morning_start) % 1440) + ((night_end - night_start) % 1440) != 1440:
        raise ValueError(f"{prefix}Morning and Night shifts must cover exactly 24 hours.")
    return clean_shifts


def _looks_like_v18_settings(settings: Dict[str, Any]) -> bool:
    if "shifts" in settings:
        return True
    return any(
        isinstance(item, dict) and item.get("data_id")
        for item in settings.get("sites") or []
    )


def _migrate_v18_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Convert the former flat Data-ID-as-site format without manual editing."""

    legacy_shifts = settings.get("shifts") or deepcopy(DEFAULT_SHIFT_SETTINGS)
    legacy_plants = list(settings.get("sites") or [])
    legacy_aliases = {
        _clean(item.get("data_id")): _clean(item.get("display_name"))
        for item in legacy_plants
        if isinstance(item, dict) and _clean(item.get("data_id"))
    }
    known_data_ids = set(legacy_aliases)
    known_data_ids.update(
        _clean(data_id)
        for data_id in (settings.get("active_benchmarks") or {}).keys()
        if _clean(data_id)
    )
    grouped: Dict[str, Dict[str, Any]] = {}
    for data_id in sorted(known_data_ids):
        site_code = derive_site_code(data_id)
        site = grouped.setdefault(
            site_code,
            {
                "site_code": site_code,
                "display_name": site_code,
                "shifts": deepcopy(legacy_shifts),
                "plants": [],
            },
        )
        site["plants"].append(
            {"data_id": data_id, "display_name": legacy_aliases.get(data_id) or data_id}
        )
    return {
        "version": int(settings.get("version") or 0),
        "timezone": _clean(settings.get("timezone")) or PLANT_TIMEZONE,
        "sites": list(grouped.values()),
        "active_benchmarks": settings.get("active_benchmarks") or {},
    }


def validate_daily_report_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Validate current settings or automatically migrated v18 settings."""

    if not isinstance(settings, dict):
        raise ValueError("Daily Report settings must be an object.")
    if _looks_like_v18_settings(settings):
        settings = _migrate_v18_settings(settings)

    try:
        version = int(settings.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Daily Report settings version is invalid.") from exc
    if version < 0:
        raise ValueError("Daily Report settings version cannot be negative.")

    timezone_name = _clean(settings.get("timezone")) or PLANT_TIMEZONE
    get_plant_timezone(timezone_name)

    clean_sites = []
    seen_site_codes = set()
    seen_data_ids = set()
    for raw_site in settings.get("sites") or []:
        if not isinstance(raw_site, dict):
            continue
        site_code = _clean(raw_site.get("site_code"))
        if not site_code:
            raise ValueError("Every site requires a site code.")
        if site_code in seen_site_codes:
            raise ValueError(f"Duplicate site code: {site_code}.")
        seen_site_codes.add(site_code)

        clean_plants = []
        for raw_plant in raw_site.get("plants") or []:
            if not isinstance(raw_plant, dict):
                continue
            data_id = _clean(raw_plant.get("data_id"))
            if not data_id:
                continue
            derived_site = derive_site_code(data_id)
            if derived_site != site_code:
                raise ValueError(f"Plant {data_id} belongs to site {derived_site}, not {site_code}.")
            if data_id in seen_data_ids:
                raise ValueError(f"Duplicate plant Data ID: {data_id}.")
            seen_data_ids.add(data_id)
            # ``sterilizer_plant_settings.display_name`` belongs to this FYP
            # module and stores only an optional operator alias.  The official
            # plant name is read from the supervisor-owned ``sites`` table and
            # must never be copied back into that table or treated as an alias.
            raw_custom_alias = raw_plant.get("custom_display_name")
            custom_alias = (
                _clean(raw_plant.get("display_name"))
                if raw_custom_alias is None
                else _clean(raw_custom_alias)
            )
            clean_plants.append(
                {
                    "data_id": data_id,
                    # The existing column is NOT NULL.  The Data ID is the
                    # backwards-compatible sentinel for "no custom alias".
                    "display_name": custom_alias or data_id,
                }
            )

        clean_plants.sort(key=lambda item: item["data_id"])
        clean_sites.append(
            {
                "site_code": site_code,
                "display_name": _clean(raw_site.get("display_name")) or site_code,
                "shifts": _validate_shifts(raw_site.get("shifts"), site_code),
                "plants": clean_plants,
            }
        )
    clean_sites.sort(key=lambda item: item["site_code"])

    clean_active: Dict[str, Dict[str, str]] = {}
    raw_active = settings.get("active_benchmarks") or {}
    if not isinstance(raw_active, dict):
        raise ValueError("Active benchmark settings must be an object.")
    for data_id, field_map in raw_active.items():
        clean_data_id = _clean(data_id)
        if not clean_data_id or not isinstance(field_map, dict):
            continue
        clean_fields = {}
        for field, file_name in field_map.items():
            clean_field = _clean(field)
            clean_file_name = os.path.basename(_clean(file_name))
            if clean_field and clean_file_name:
                clean_fields[clean_field] = clean_file_name
        if clean_fields:
            clean_active[clean_data_id] = clean_fields
    return {
        "version": version,
        "timezone": timezone_name,
        "sites": clean_sites,
        "active_benchmarks": clean_active,
    }


def _format_database_time(value: Any) -> str:
    """Convert a Postgres time such as 12:00:00 to the UI's HH:MM form."""

    text = _clean(value)
    if not text:
        return ""
    return text[:5]


def _rows_for_ids(table: str, columns: str, identifiers: list[str]) -> list[Dict[str, Any]]:
    if not identifiers:
        return []
    return get_supabase_client().select(
        table,
        columns=columns,
        filters={"id": postgres_in_filter(identifiers)},
    )


def _load_settings_from_supabase() -> Dict[str, Any]:
    client = get_supabase_client()
    metadata_rows = client.select(
        "sterilizer_settings_metadata",
        columns="settings_key,version,timezone",
        filters={"settings_key": f"eq.{SETTINGS_METADATA_KEY}"},
        limit=1,
    )
    metadata = metadata_rows[0] if metadata_rows else {}

    shift_rows = client.select(
        "site_shift_settings",
        columns=(
            "site_code,site_display_name,morning_start,morning_end,"
            "night_start,night_end"
        ),
        order="site_code.asc",
    )
    plant_rows = client.select(
        "sterilizer_plant_settings",
        columns="data_id,site_code,display_name",
        order="data_id.asc",
    )

    plants_by_site: Dict[str, list[Dict[str, str]]] = {}
    for row in plant_rows:
        site_code = _clean(row.get("site_code"))
        data_id = _clean(row.get("data_id"))
        if site_code and data_id:
            plants_by_site.setdefault(site_code, []).append(
                {
                    "data_id": data_id,
                    "display_name": _clean(row.get("display_name")) or data_id,
                }
            )

    sites = []
    for row in shift_rows:
        site_code = _clean(row.get("site_code"))
        if not site_code:
            continue
        sites.append(
            {
                "site_code": site_code,
                "display_name": _clean(row.get("site_display_name")) or site_code,
                "shifts": {
                    "morning": {
                        "label": "Morning Shift",
                        "start": _format_database_time(row.get("morning_start")),
                        "end": _format_database_time(row.get("morning_end")),
                    },
                    "night": {
                        "label": "Night Shift",
                        "start": _format_database_time(row.get("night_start")),
                        "end": _format_database_time(row.get("night_end")),
                    },
                },
                "plants": plants_by_site.get(site_code, []),
            }
        )

    active_rows = client.select(
        "sterilizer_active_benchmarks",
        columns="equipment_id,channel_id,benchmark_id",
    )
    equipment_ids = sorted(
        {_clean(row.get("equipment_id")) for row in active_rows if row.get("equipment_id")}
    )
    benchmark_ids = sorted(
        {_clean(row.get("benchmark_id")) for row in active_rows if row.get("benchmark_id")}
    )
    equipment_by_id = {
        _clean(row.get("id")): row
        for row in _rows_for_ids("equipment", "id,device_id", equipment_ids)
    }
    benchmark_by_id = {
        _clean(row.get("id")): row
        for row in _rows_for_ids(
            "sterilizer_benchmarks",
            "id,file_name,tag_id,field",
            benchmark_ids,
        )
    }
    active_benchmarks: Dict[str, Dict[str, str]] = {}
    for row in active_rows:
        equipment = equipment_by_id.get(_clean(row.get("equipment_id"))) or {}
        benchmark = benchmark_by_id.get(_clean(row.get("benchmark_id"))) or {}
        data_id = _clean(equipment.get("device_id"))
        field = _clean(row.get("channel_id"))
        file_name = _clean(benchmark.get("file_name"))
        if not data_id or not field or not file_name:
            continue
        if _clean(benchmark.get("tag_id")) != data_id or _clean(benchmark.get("field")) != field:
            raise SettingsStorageError(
                "An active benchmark assignment does not match its plant or sterilizer."
            )
        active_benchmarks.setdefault(data_id, {})[field] = file_name

    return validate_daily_report_settings(
        {
            "version": int(metadata.get("version") or 0),
            "timezone": _clean(metadata.get("timezone")) or PLANT_TIMEZONE,
            "sites": sites,
            "active_benchmarks": active_benchmarks,
        }
    )


def load_daily_report_settings_with_status() -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Load all operator-managed settings from Supabase."""

    try:
        return _load_settings_from_supabase(), None
    except SettingsStorageError:
        raise
    except Exception as exc:
        raise SettingsStorageError(
            "Daily Report settings could not be loaded from Supabase."
        ) from exc


def load_daily_report_settings() -> Dict[str, Any]:
    settings, _status = load_daily_report_settings_with_status()
    return settings


def load_official_plant_names(data_ids: list[str]) -> Dict[str, str]:
    """Read the official Data-ID/name mapping without modifying source tables.

    The company schema links ``equipment.device_id`` to ``sites`` through
    ``equipment.site_id``.  A device is returned only when it resolves to one
    unambiguous site.  Any lookup problem falls back to the Data ID so naming
    can never block detection, monitoring, or report generation.
    """

    identifiers = sorted({_clean(value) for value in data_ids if _clean(value)})
    if not identifiers:
        return {}

    try:
        client = get_supabase_client()
        equipment_rows = client.select(
            "equipment",
            columns="device_id,site_id",
            filters={"device_id": postgres_in_filter(identifiers)},
        )

        site_ids_by_device: Dict[str, set[str]] = {}
        for row in equipment_rows:
            device_id = _clean(row.get("device_id"))
            site_id = _clean(row.get("site_id"))
            if device_id and site_id:
                site_ids_by_device.setdefault(device_id, set()).add(site_id)

        unambiguous_site_ids = sorted(
            {
                next(iter(site_ids))
                for site_ids in site_ids_by_device.values()
                if len(site_ids) == 1
            }
        )
        site_rows = _rows_for_ids(
            "sites",
            "id,name",
            unambiguous_site_ids,
        )
        names_by_site_id = {
            _clean(row.get("id")): _clean(row.get("name"))
            for row in site_rows
            if _clean(row.get("id")) and _clean(row.get("name"))
        }

        official_names: Dict[str, str] = {}
        for device_id, site_ids in site_ids_by_device.items():
            if len(site_ids) != 1:
                if len(site_ids) > 1:
                    LOGGER.warning(
                        "Official plant mapping ignored for ambiguous device_id=%s",
                        device_id,
                    )
                continue
            site_name = names_by_site_id.get(next(iter(site_ids)))
            if site_name:
                official_names[device_id] = site_name
        return official_names
    except Exception as exc:
        LOGGER.warning("Official plant-name lookup failed: %s", exc)
        return {}


def load_plant_custom_aliases(data_ids: list[str]) -> Dict[str, str]:
    """Load only genuine aliases from the FYP-owned settings table."""

    identifiers = sorted({_clean(value) for value in data_ids if _clean(value)})
    if not identifiers:
        return {}
    try:
        rows = get_supabase_client().select(
            "sterilizer_plant_settings",
            columns="data_id,display_name",
            filters={"data_id": postgres_in_filter(identifiers)},
        )
        aliases: Dict[str, str] = {}
        for row in rows:
            data_id = _clean(row.get("data_id"))
            display_name = _clean(row.get("display_name"))
            if data_id and display_name and display_name != data_id:
                aliases[data_id] = display_name
        return aliases
    except Exception as exc:
        LOGGER.warning("Custom plant-alias lookup failed: %s", exc)
        return {}


def get_plant_custom_alias(settings: Dict[str, Any], data_id: str) -> str:
    """Return a real operator alias, excluding the legacy Data-ID sentinel."""

    target = _clean(data_id)
    site = get_site_config(settings, derive_site_code(target))
    for plant in (site or {}).get("plants") or []:
        if _clean(plant.get("data_id")) != target:
            continue
        stored_name = _clean(plant.get("display_name"))
        return stored_name if stored_name and stored_name != target else ""
    return ""


def get_plant_name_details(
    settings: Dict[str, Any],
    data_id: str,
    official_names: Optional[Dict[str, str]] = None,
    custom_aliases: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Return separate custom, official, and effective display names."""

    target = _clean(data_id)
    custom_alias = (
        _clean((custom_aliases or {}).get(target))
        if custom_aliases is not None
        else get_plant_custom_alias(settings, target)
    )
    official_map = (
        official_names
        if official_names is not None
        else load_official_plant_names([target])
    )
    official_name = _clean((official_map or {}).get(target))
    return {
        "data_id": target,
        "custom_display_name": custom_alias,
        "official_name": official_name,
        "display_name": custom_alias or official_name or target,
    }


def _resolve_active_assignments(
    active_benchmarks: Dict[str, Dict[str, str]],
) -> list[Dict[str, str]]:
    """Resolve user-visible filenames to existing database relationships.

    The Settings API keeps filenames in its public contract because that is
    what operators recognise. The atomic Supabase function receives UUIDs so
    it does not have to repeat equipment discovery, depend on a hardcoded
    measurement name, or identify a benchmark by a mutable display value.
    """

    resolved: list[Dict[str, str]] = []
    for data_id in sorted(active_benchmarks):
        field_map = active_benchmarks.get(data_id) or {}
        for field in sorted(field_map):
            file_name = field_map[field]
            benchmark = load_benchmark(file_name)
            validate_benchmark_compatibility(
                benchmark,
                tag_id=data_id,
                field=field,
                file_name=file_name,
            )
            benchmark_id = _clean(
                benchmark.get("database_id") or benchmark.get("benchmark_id")
            )
            equipment_id = _clean(benchmark.get("equipment_id"))
            if not benchmark_id or not equipment_id:
                raise ValueError(
                    f"Benchmark {file_name} is missing its Supabase equipment or "
                    "benchmark ID. Recreate the benchmark before assigning it."
                )
            resolved.append(
                {
                    "equipment_id": equipment_id,
                    "channel_id": field,
                    "benchmark_id": benchmark_id,
                }
            )
    return resolved


def save_daily_report_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    clean_settings = validate_daily_report_settings(settings)
    current = load_daily_report_settings()
    submitted_version = int(clean_settings.get("version") or 0)
    current_version = int(current.get("version") or 0)
    if submitted_version != current_version:
        raise SettingsConflictError(
            "Settings were changed by another user or browser session. "
            "Reload the latest settings before saving your changes."
        )

    # Resolve once before the transaction. The RPC receives stable UUID
    # relationships while the Settings response remains filename-based.
    resolved_active_assignments = _resolve_active_assignments(
        clean_settings.get("active_benchmarks") or {}
    )

    try:
        get_supabase_client().rpc(
            "replace_sterilizer_settings",
            {
                "p_expected_version": submitted_version,
                "p_timezone": clean_settings["timezone"],
                "p_sites": clean_settings["sites"],
                "p_active_benchmarks": resolved_active_assignments,
            },
        )
    except SupabaseRestError as exc:
        if exc.code == "40001" or "version conflict" in str(exc).lower():
            raise SettingsConflictError(
                "Settings were changed by another user or browser session. "
                "Reload the latest settings before saving your changes."
            ) from exc
        if exc.code == "PGRST202" or exc.status_code == 404:
            public_message = (
                "Settings save failed [SETTINGS_RPC_UNAVAILABLE] because the Supabase "
                "replace_sterilizer_settings function is unavailable or has a "
                "different signature."
            )
        elif exc.code == "P0001":
            public_message = (
                "Settings save failed [SETTINGS_ACTIVE_ASSIGNMENT_INVALID] because "
                "Supabase found an inconsistent benchmark assignment. Reload Settings "
                "and reselect the affected benchmark."
            )
        elif exc.code == "22P02":
            public_message = (
                "Settings save failed [SETTINGS_IDENTIFIER_INVALID] because Supabase rejected an active "
                "benchmark identifier. Reload Settings and reselect the affected benchmark."
            )
        elif exc.code == "23503":
            public_message = (
                "Settings save failed [SETTINGS_REFERENCE_MISSING] because an assigned plant or benchmark "
                "no longer exists. Reload Settings and review the assignments."
            )
        elif exc.code in {"23502", "23505", "23514", "42P10"}:
            public_message = (
                "Settings save failed [SETTINGS_SCHEMA_CONSTRAINT] because the stored "
                "Supabase schema rejected one of the validated settings records."
            )
        elif exc.status_code in {401, 403}:
            public_message = (
                "Settings save failed [SETTINGS_PERMISSION_DENIED] because the backend Supabase key does "
                "not have permission to run the settings update."
            )
        else:
            public_message = "Settings save failed [SETTINGS_DATABASE_REJECTED]."
        raise SettingsStorageError(
            f"Daily Report settings could not be saved to Supabase: {exc}",
            public_message=public_message,
        ) from exc

    saved_settings = load_daily_report_settings()
    audit_result = try_record_audit_event(
        action="daily_report_settings_saved",
        entity_type="daily_report_settings",
        entity_id="daily_report_settings",
        before=current,
        after=saved_settings,
        details={
            "previous_version": current_version,
            "saved_version": saved_settings["version"],
            "storage": "supabase",
        },
    )
    response_settings = deepcopy(saved_settings)
    response_settings["_audit_status"] = audit_result["status"]
    return response_settings


def get_site_config(settings: Dict[str, Any], site_code: str) -> Optional[Dict[str, Any]]:
    target = _clean(site_code)
    for site in settings.get("sites") or []:
        if _clean(site.get("site_code")) == target:
            return site
    return None


def get_site_display_name(settings: Dict[str, Any], site_code: str) -> str:
    site = get_site_config(settings, site_code)
    return (_clean(site.get("display_name")) if site else "") or _clean(site_code)


def get_site_shifts(settings: Dict[str, Any], site_code: str) -> Dict[str, Any]:
    site = get_site_config(settings, site_code)
    return deepcopy(site.get("shifts")) if site else deepcopy(DEFAULT_SHIFT_SETTINGS)


def get_plant_display_name(
    settings: Dict[str, Any],
    data_id: str,
    official_names: Optional[Dict[str, str]] = None,
) -> str:
    return get_plant_name_details(
        settings,
        data_id,
        official_names=official_names,
    )["display_name"]


def get_active_benchmark_file_name(
    settings: Dict[str, Any],
    data_id: str,
    field: str,
) -> Optional[str]:
    """Return the configured active benchmark file for one plant/sterilizer.

    Active benchmark assignments are stored in Supabase and exposed through the
    validated settings structure as ``active_benchmarks[data_id][field]``.
    ``None`` is returned when no assignment exists.
    """

    clean_data_id = _clean(data_id)
    clean_field = _clean(field)
    if not clean_data_id or not clean_field:
        return None

    field_map = (settings.get("active_benchmarks") or {}).get(clean_data_id) or {}
    file_name = _clean(field_map.get(clean_field))
    return file_name or None
