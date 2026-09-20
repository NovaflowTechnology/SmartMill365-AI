"""Minimal server-side client for the Supabase Data REST API.

The project uses the existing ``httpx`` dependency instead of adding another
SDK.  New ``sb_secret_`` keys are sent through the required ``apikey`` header.
Legacy service-role JWT keys are also supported during Supabase's migration
period.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.config import (
    SUPABASE_SECRET_KEY,
    SUPABASE_TIMEOUT_SECONDS,
    SUPABASE_URL,
)


class SupabaseRestError(RuntimeError):
    """A sanitized Supabase Data API failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def is_supabase_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def check_supabase_connectivity() -> Dict[str, Any]:
    """Run a small real database request for deployment readiness checks."""

    if not is_supabase_configured():
        return {"ok": False, "detail": "Supabase is not configured."}
    try:
        client = get_supabase_client()
        table_checks = (
            ("sterilizer_benchmarks", "id"),
            ("sterilizer_analysis_rules", "rule_id"),
            ("sterilizer_analysis_reference_chunks", "chunk_id"),
            ("sterilizer_audit_events", "event_id"),
        )
        for table, key_column in table_checks:
            client.select(table, columns=key_column, limit=1)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


class SupabaseRestClient:
    def __init__(self, url: str, secret_key: str, timeout_seconds: float = 20) -> None:
        clean_url = str(url or "").strip().rstrip("/")
        clean_key = str(secret_key or "").strip()
        if not clean_url or not clean_key:
            raise SupabaseRestError(
                "Supabase is not configured. Add SUPABASE_URL and "
                "SUPABASE_SECRET_KEY to the backend environment."
            )

        headers = {
            "apikey": clean_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # Opaque sb_secret keys are not JWTs. Legacy service-role keys are.
        if clean_key.count(".") == 2:
            headers["Authorization"] = f"Bearer {clean_key}"

        self._base_url = f"{clean_url}/rest/v1"
        self._client = httpx.Client(
            timeout=float(timeout_seconds),
            headers=headers,
        )

    @staticmethod
    def _error_details(response: httpx.Response) -> tuple[str, Optional[str]]:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            message = str(
                payload.get("message")
                or payload.get("hint")
                or "Supabase rejected the database request."
            )
            code = payload.get("code")
            return message, str(code) if code is not None else None
        return "Supabase rejected the database request.", None

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        prefer: Optional[str] = None,
    ) -> Any:
        headers = {"Prefer": prefer} if prefer else None
        try:
            response = self._client.request(
                method,
                f"{self._base_url}/{path.lstrip('/')}",
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise SupabaseRestError(
                "Supabase database is temporarily unavailable."
            ) from exc

        if response.is_error:
            message, code = self._error_details(response)
            raise SupabaseRestError(
                message,
                status_code=response.status_code,
                code=code,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SupabaseRestError(
                "Supabase returned an unreadable response.",
                status_code=response.status_code,
            ) from exc

    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Optional[Dict[str, str]] = None,
        order: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"select": columns}
        params.update(filters or {})
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = int(limit)
        result = self.request("GET", table, params=params)
        return list(result or [])

    def insert(
        self,
        table: str,
        rows: Dict[str, Any] | List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        result = self.request(
            "POST",
            table,
            json_body=rows,
            prefer="return=representation",
        )
        return list(result or [])

    def upsert(
        self,
        table: str,
        rows: Dict[str, Any] | List[Dict[str, Any]],
        *,
        on_conflict: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params = {"on_conflict": on_conflict} if on_conflict else None
        result = self.request(
            "POST",
            table,
            params=params,
            json_body=rows,
            prefer="resolution=merge-duplicates,return=representation",
        )
        return list(result or [])

    def update(
        self,
        table: str,
        values: Dict[str, Any],
        *,
        filters: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        result = self.request(
            "PATCH",
            table,
            params=dict(filters or {}),
            json_body=values,
            prefer="return=representation",
        )
        return list(result or [])

    def delete(
        self,
        table: str,
        *,
        filters: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        result = self.request(
            "DELETE",
            table,
            params=dict(filters or {}),
            prefer="return=representation",
        )
        return list(result or [])

    def rpc(self, function_name: str, arguments: Dict[str, Any]) -> Any:
        return self.request(
            "POST",
            f"rpc/{function_name}",
            json_body=arguments,
            prefer="return=representation",
        )


def postgres_in_filter(values: Iterable[Any]) -> str:
    clean_values = [str(value).strip() for value in values if str(value).strip()]
    if not clean_values:
        return "in.()"
    return "in.(" + ",".join(clean_values) + ")"


@lru_cache(maxsize=1)
def get_supabase_client() -> SupabaseRestClient:
    return SupabaseRestClient(
        SUPABASE_URL,
        SUPABASE_SECRET_KEY,
        SUPABASE_TIMEOUT_SECONDS,
    )
