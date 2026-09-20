"""Consistent public API errors and request references."""

from __future__ import annotations

import contextvars
import json
import logging
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException


LOGGER = logging.getLogger("sterilizer_api")
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "sterilizer_request_id", default=""
)


class SettingsConflictError(ValueError):
    """The browser edited an older settings version."""


class SettingsStorageError(RuntimeError):
    """Settings or its last-known-good backup could not be recovered."""

    def __init__(self, message: str, *, public_message: Optional[str] = None) -> None:
        super().__init__(message)
        self.public_message = public_message


def current_request_id() -> str:
    return REQUEST_ID.get() or uuid4().hex[:12]


def friendly_error_message(error: Exception) -> str:
    message = str(error).strip()
    lower_msg = message.lower()

    if (
        "no data returned from influxdb" in lower_msg
        or "no data found for the selected time range" in lower_msg
        or "no usable pressure values" in lower_msg
        or "empty dataframe" in lower_msg
        or "index 0 is out of bounds" in lower_msg
        or "single positional indexer is out-of-bounds" in lower_msg
    ):
        return "No data found for the selected time range and Data ID."
    if "cannot parse date" in lower_msg or "invalid date" in lower_msg:
        return "Invalid date or time format."
    if "no unit found in data" in lower_msg or "unit could not be inferred" in lower_msg:
        return "Pressure unit could not be derived from the selected pressure measurement."
    if "mixed units found" in lower_msg:
        return "Multiple units were found in the selected data. Please check the selected source."
    if "benchmark file name is required" in lower_msg:
        return "Please choose a benchmark before running comparison."
    if "benchmark file not found" in lower_msg:
        return "Selected benchmark file could not be found."
    if "no cycles detected in the selected real-time range" in lower_msg:
        return "No complete cycle was detected in the selected real-time range."
    if "cycle too short" in lower_msg:
        return "Detected cycle is too short for comparison."
    if "value_std" in lower_msg:
        return (
            "No usable standardised pressure data was found for the selected range. "
            "Check the Data ID, sterilizer, pressure unit, and time range."
        )
    return message


def _looks_like_service_failure(error: Exception) -> bool:
    module_name = error.__class__.__module__.lower()
    text = str(error).lower()
    return (
        module_name.startswith("influxdb_client")
        or module_name.startswith("qdrant_client")
        or module_name.startswith("httpx")
        or "supabase database is temporarily unavailable" in text
        or "connection refused" in text
        or "failed to establish a new connection" in text
        or "database is unavailable" in text
    )


def http_exception_from_error(
    error: Exception, *, default_status: Optional[int] = None
) -> HTTPException:
    if isinstance(error, HTTPException):
        return error

    request_id = current_request_id()
    LOGGER.exception("Request %s failed", request_id, exc_info=error)

    if isinstance(error, SettingsConflictError):
        return HTTPException(status_code=409, detail=friendly_error_message(error))
    if isinstance(error, FileNotFoundError):
        return HTTPException(status_code=404, detail=friendly_error_message(error))
    if isinstance(error, ValueError):
        return HTTPException(
            status_code=default_status or 400,
            detail=friendly_error_message(error),
        )
    if isinstance(error, (SettingsStorageError, OSError, json.JSONDecodeError)):
        public_message = getattr(error, "public_message", None)
        return HTTPException(
            status_code=500,
            detail=(
                f"{public_message} Reference: {request_id}."
                if public_message
                else f"A stored configuration could not be read or saved. Reference: {request_id}."
            ),
        )
    if _looks_like_service_failure(error):
        return HTTPException(
            status_code=503,
            detail=f"A required data service is temporarily unavailable. Reference: {request_id}.",
        )
    return HTTPException(
        status_code=default_status or 500,
        detail=f"The request could not be completed. Reference: {request_id}.",
    )


def raise_http_error(error: Exception, *, default_status: Optional[int] = None) -> None:
    raise http_exception_from_error(error, default_status=default_status) from error
